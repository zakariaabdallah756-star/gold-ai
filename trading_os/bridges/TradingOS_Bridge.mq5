//+------------------------------------------------------------------+
//| TradingOS_Bridge.mq5 - Trading OS order bridge for MetaTrader 5   |
//+------------------------------------------------------------------+
//
// Places the orders you confirm in Trading OS ("Execute on <account>" on the Signals page) on the
// MetaTrader 5 account this Expert Advisor runs on, and reports fills and account figures back.
//
// SETUP
//  1. In MetaTrader 5: File > Open Data Folder > MQL5 > Experts, copy this file there, then open it in
//     MetaEditor and press Compile (F7).
//  2. Tools > Options > Expert Advisors: tick "Allow WebRequest for listed URL" and add the Trading OS
//     address exactly as you type it in the ServerUrl input below, e.g. http://127.0.0.1:8765
//     (use 127.0.0.1 rather than localhost; from a VPS, the address of the computer running Trading OS).
//  3. In Trading OS: Trading Accounts > the account > Connection = "MetaTrader EA bridge". Create the bridge
//     token there (on the computer running Trading OS) and copy it.
//  4. Drag this EA onto ONE chart (any symbol), paste the token in the inputs, tick "Allow Algo Trading"
//     and switch Algo Trading on in the toolbar. The Experts tab logs every order.
//
// TEST ON A DEMO ACCOUNT FIRST. Trading OS only sends orders you confirm and that pass your guardrails,
// but this EA places real orders on whatever account it runs on.
//
// PROTOCOL (plain text, no JSON; the token travels in the X-Bridge-Token header)
//   GET  {ServerUrl}/api/bridge/orders       -> one order per line: id|symbol|side|type|lots|price|sl|tp
//                                              side = buy|sell, type = market|limit|stop, 0 = none
//   POST {ServerUrl}/api/bridge/orders/{id}  <- status=filled|placed|rejected&ticket=..&price=..&message=..
//   POST {ServerUrl}/api/bridge/account      <- balance=..&equity=..&margin=..&free_margin=..&profit=..&positions=..
// Trading OS hands each order out once; this EA also remembers the last order id it handled, so an order
// is never placed twice.
#property copyright "Trading OS"
#property version   "1.00"
#property description "Places the orders confirmed in Trading OS and reports fills and account figures."

#include <Trade/Trade.mqh>

input string InpServerUrl    = "http://127.0.0.1:8765"; // ServerUrl: Trading OS address (allowed under WebRequest)
input string InpToken        = "";                      // Token: the account's bridge token
input int    InpPollSeconds  = 3;                       // Poll every N seconds
input string InpSymbolSuffix = "";                      // Symbol suffix your broker adds (e.g. .m), if any
input long   InpMagic        = 20250;                   // Magic number of the orders it places
input int    InpMaxSlippage  = 20;                      // Max slippage for market orders, in points

CTrade   g_trade;
long     g_lastId = 0;        // highest order id handled (kept in a terminal global variable)
string   g_lastIdKey = "";
datetime g_lastAccount = 0;   // last time the account figures were sent

//--- start: check the inputs, restore the last handled id, start the timer
int OnInit()
{
   if(StringLen(InpToken) < 16)
   {
      Print("Trading OS: paste the account's bridge token in the EA inputs");
      return(INIT_PARAMETERS_INCORRECT);
   }
   g_trade.SetExpertMagicNumber((ulong)InpMagic);
   g_trade.SetDeviationInPoints((ulong)MathMax(InpMaxSlippage, 0));
   g_trade.SetAsyncMode(false);
   // One key per account and token: a new Trading OS install (new token) starts counting again.
   g_lastIdKey = "TradingOS_LastId_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_" + StringSubstr(InpToken, 0, 6);
   if(GlobalVariableCheck(g_lastIdKey))
      g_lastId = (long)GlobalVariableGet(g_lastIdKey);
   EventSetTimer(MathMax(InpPollSeconds, 1));
   Print("Trading OS bridge started: polling ", BaseUrl(), " every ", MathMax(InpPollSeconds, 1), " s");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

//--- every poll: fetch and place new orders, then (every 10 s) send the account figures
void OnTimer()
{
   PollOrders();
   if(TimeLocal() - g_lastAccount >= 10)
   {
      g_lastAccount = TimeLocal();
      SendAccount();
   }
}

//--- the server address without a trailing slash
string BaseUrl()
{
   string url = InpServerUrl;
   while(StringLen(url) > 0 && StringGetCharacter(url, StringLen(url) - 1) == '/')
      url = StringSubstr(url, 0, StringLen(url) - 1);
   return(url);
}

//--- one HTTP call; returns the HTTP status (-1 = WebRequest failed) and the body in `response`
int Http(const string method, const string path, const string body, string &response)
{
   string headers = "X-Bridge-Token: " + InpToken + "\r\n"
                    + "Content-Type: application/x-www-form-urlencoded\r\n";
   char   data[];
   char   result[];
   string resultHeaders;
   if(StringLen(body) > 0)
      StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);  // the body is plain ASCII (URL-encoded)
   ResetLastError();
   int status = WebRequest(method, BaseUrl() + path, headers, 5000, data, result, resultHeaders);
   if(status == -1)
   {
      Print("Trading OS: WebRequest failed (error ", GetLastError(), "). Is Trading OS running, and is ",
            BaseUrl(), " listed under Tools > Options > Expert Advisors > Allow WebRequest?");
      response = "";
      return(-1);
   }
   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   return(status);
}

//--- percent-encode a message for a form body
string UrlEncode(const string text)
{
   string out = "";
   uchar bytes[];
   int n = StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8);
   for(int i = 0; i < n; i++)
   {
      uchar c = bytes[i];
      if(c == 0)
         break;
      if((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.')
         out += CharToString(c);
      else
         out += StringFormat("%%%02X", c);
   }
   return(out);
}

//--- fetch the queued orders and place each new one
void PollOrders()
{
   string body;
   int status = Http("GET", "/api/bridge/orders", "", body);
   if(status == 401)
   {
      Print("Trading OS: the bridge token was refused - copy it again from Trading Accounts");
      return;
   }
   if(status != 200)
   {
      if(status > 0)
         Print("Trading OS: poll answered HTTP ", status, ": ", body);
      return;
   }
   string lines[];
   int count = StringSplit(body, '\n', lines);
   for(int i = 0; i < count; i++)
   {
      string line = lines[i];
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) > 0)
         ExecuteLine(line);
   }
}

//--- the broker's name of a symbol: as sent, else with the suffix input
string ResolveSymbol(const string name)
{
   if(SymbolSelect(name, true))
      return(name);
   if(StringLen(InpSymbolSuffix) > 0 && SymbolSelect(name + InpSymbolSuffix, true))
      return(name + InpSymbolSuffix);
   return("");
}

//--- lots rounded down to the symbol's volume step; 0 = below the minimum, -1 = above the maximum
double NormalizeLots(const string symbol, double lots)
{
   double step   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   if(step > 0)
      lots = MathFloor(lots / step + 1e-9) * step;
   if(lots < minLot)
      return(0);
   if(maxLot > 0 && lots > maxLot)
      return(-1);
   return(NormalizeDouble(lots, 8));
}

//--- remember the id BEFORE placing the order: never place it twice, even after a crash
void RememberId(const long id)
{
   g_lastId = id;
   GlobalVariableSet(g_lastIdKey, (double)id);
}

//--- place one order line "id|symbol|side|type|lots|price|sl|tp" and report the result
void ExecuteLine(const string line)
{
   string f[];
   if(StringSplit(line, '|', f) != 8)
   {
      Print("Trading OS: unreadable order line: ", line);
      return;
   }
   long id = StringToInteger(f[0]);
   if(id <= g_lastId)
      return;  // already handled
   RememberId(id);

   string symbol = ResolveSymbol(f[1]);
   string side   = f[2];
   string type   = f[3];
   if(symbol == "")
   {
      Report(id, "rejected", 0, 0, "Symbol " + f[1] + " is not available on this account");
      return;
   }
   double lots = NormalizeLots(symbol, StringToDouble(f[4]));
   if(lots == 0)
   {
      Report(id, "rejected", 0, 0, "Volume below the minimum lot of " + symbol);
      return;
   }
   if(lots < 0)
   {
      Report(id, "rejected", 0, 0, "Volume above the maximum lot of " + symbol);
      return;
   }
   int    digits  = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double price   = NormalizeDouble(StringToDouble(f[5]), digits);
   double sl      = NormalizeDouble(StringToDouble(f[6]), digits);
   double tp      = NormalizeDouble(StringToDouble(f[7]), digits);
   bool   buy     = (side == "buy");
   string comment = "TradingOS #" + IntegerToString(id);
   bool   ok      = false;

   g_trade.SetTypeFillingBySymbol(symbol);
   if(type == "market")
      ok = buy ? g_trade.Buy(lots, symbol, 0.0, sl, tp, comment) : g_trade.Sell(lots, symbol, 0.0, sl, tp, comment);
   else if(type == "limit")
      ok = buy ? g_trade.BuyLimit(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
               : g_trade.SellLimit(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else if(type == "stop")
      ok = buy ? g_trade.BuyStop(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
               : g_trade.SellStop(lots, price, symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
   else
   {
      Report(id, "rejected", 0, 0, "Unknown order type " + type);
      return;
   }

   uint code = g_trade.ResultRetcode();
   if(!ok || (code != TRADE_RETCODE_DONE && code != TRADE_RETCODE_PLACED && code != TRADE_RETCODE_DONE_PARTIAL))
   {
      Report(id, "rejected", 0, 0, g_trade.ResultRetcodeDescription() + " (" + IntegerToString(code) + ")");
      return;
   }
   if(type == "market")
   {
      double fill = g_trade.ResultPrice();
      if(fill <= 0)
         fill = buy ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID);
      // A partial fill reports the volume really filled, so the journal holds the true size.
      double volume = g_trade.ResultVolume();
      Report(id, "filled", g_trade.ResultOrder(), fill, "", code == TRADE_RETCODE_DONE_PARTIAL ? volume : 0);
   }
   else
      Report(id, "placed", g_trade.ResultOrder(), price, "");
}

//--- report one order's result (a few tries if Trading OS does not answer)
void Report(const long id, const string status, const ulong ticket, const double price, const string message,
            const double volume = 0)
{
   string body = "status=" + status
                 + "&ticket=" + IntegerToString((long)ticket)
                 + "&price=" + DoubleToString(price, 8)
                 + "&message=" + UrlEncode(message);
   if(volume > 0)
      body += "&volume=" + DoubleToString(volume, 2);
   string response;
   int code = -1;
   for(int attempt = 0; attempt < 3; attempt++)
   {
      code = Http("POST", "/api/bridge/orders/" + IntegerToString(id), body, response);
      if(code == 200 || code == 401 || code == 404 || code == 409)
         break;  // a final answer
      Sleep(1000);
   }
   Print("Trading OS: order ", id, " ", status, (ticket > 0 ? " ticket " + IntegerToString((long)ticket) : ""),
         (StringLen(message) > 0 ? " - " + message : ""), " (reported: HTTP ", code, ")");
}

//--- balance, equity, margin and open positions for the Trading OS account metrics
void SendAccount()
{
   string body = "balance=" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2)
                 + "&equity=" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2)
                 + "&margin=" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2)
                 + "&free_margin=" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2)
                 + "&margin_level=" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2)
                 + "&profit=" + DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT), 2)
                 + "&positions=" + IntegerToString(PositionsTotal());
   string response;
   Http("POST", "/api/bridge/account", body, response);
}
//+------------------------------------------------------------------+
