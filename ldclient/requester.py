from __future__ import absolute_import

import errno
import json
from threading import Thread

import requests
from cachecontrol import CacheControl
from requests.packages.urllib3.exceptions import ProtocolError

from ldclient.interfaces import EventConsumer, FeatureRequester
from ldclient.util import _headers
from ldclient.util import log


class FeatureRequesterImpl(FeatureRequester):
    def __init__(self, api_key, config):
        self._api_key = api_key
        self._session = CacheControl(requests.Session())
        self._config = config

    def getAll(self):
        hdrs = _headers(self._api_key)
        uri = self._config.get_latest_features_uri
        r = self._session.get(uri, headers=hdrs, timeout=(
            self._config.connect, self._config.read_timeout))
        r.raise_for_status()
        features = r.json()
        return features

    def get(self, key):
        hdrs = _headers(self._api_key)
        uri = self._config.get_latest_features_uri + '/' + key
        r = self._session.get(uri, headers=hdrs, timeout=(
            self._config.connect, self._config.read_timeout))
        r.raise_for_status()
        feature = r.json()
        return feature


class RequestsEventConsumer(Thread, EventConsumer):
    def __init__(self, event_queue, api_key, config):
        Thread.__init__(self)
        self._session = requests.Session()
        self.daemon = True
        self._api_key = api_key
        self._config = config
        self._queue = event_queue
        self._running = False

    def run(self):
        log.debug("Starting event consumer")
        self._running = True
        while self._running:
            self.send()

    def stop(self):
        self._running = False

    def flush(self):
        self._queue.join()

    def send_batch(self, events):
        def do_send(should_retry):
            # noinspection PyBroadException
            try:
                if isinstance(events, dict):
                    body = [events]
                else:
                    body = events
                hdrs = _headers(self._api_key)
                uri = self._config.events_uri + '/bulk'
                r = self._session.post(uri, headers=hdrs, timeout=(self._config.connect, self._config.read_timeout),
                                       data=json.dumps(body))
                r.raise_for_status()
            except ProtocolError as e:
                inner = e.args[1]
                if inner.errno == errno.ECONNRESET and should_retry:
                    log.warning(
                        'ProtocolError exception caught while sending events. Retrying.')
                    do_send(False)
                else:
                    log.exception(
                        'Unhandled exception in event consumer. Analytics events were not processed.')
            except:
                log.exception(
                    'Unhandled exception in event consumer. Analytics events were not processed.')

        try:
            do_send(True)
        finally:
            for _ in events:
                self._queue.task_done()

    def send(self):
        events = self.next()

        if len(events) == 0:
            return
        else:
            self.send_batch(events)

    def next(self):
        q = self._queue
        items = []

        item = self.next_item()
        if item is None:
            return items

        items.append(item)
        while len(items) < self._config.upload_limit and not q.empty():
            item = self.next_item()
            if item:
                items.append(item)

        return items

    def next_item(self):
        q = self._queue
        # noinspection PyBroadException
        try:
            item = q.get(block=True, timeout=5)
            return item
        except Exception:
            return None
