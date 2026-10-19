// Thin fetch wrapper around the REST API.

export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

function describe(payload) {
  if (!payload) return "Request failed";
  if (typeof payload === "string") return payload;
  const { detail } = payload;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((err) => {
        const msg = String(err.msg || "Invalid value").replace(/^Value error, /, "");
        const field = (err.loc || []).filter((part) => part !== "body").join(".");
        return field && !/^\d+$/.test(field) && err.type !== "value_error" ? `${field}: ${msg}` : msg;
      })
      .join(" · ");
  }
  return "Request failed";
}

async function request(method, path, body, { form } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (form) {
    options.body = form;
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, "Cannot reach the Trading OS server. Is it still running?");
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new ApiError(response.status, describe(payload), payload);
  return payload;
}

export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, undefined, { form }),
};
