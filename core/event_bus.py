from collections import defaultdict


class EventBus:
    def __init__(self):
        self._subscribers = defaultdict(list)

    def subscribe(self, event, callback):
        self._subscribers[event].append(callback)

    def publish(self, event, data=None):
        for callback in self._subscribers[event]:
            callback(data)