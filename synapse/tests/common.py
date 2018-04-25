import os
import socket
import logging

import unittest.mock as mock


loglevel = os.getenv('SYN_TEST_LOG_LEVEL', 'WARNING')
logging.basicConfig(level=loglevel,
                    format='%(asctime)s [%(levelname)s] %(message)s [%(filename)s:%(funcName)s:%(threadName)s:%(processName)s]')

import synapse.link as s_link
import synapse.common as s_common
import synapse.cortex as s_cortex
import synapse.daemon as s_daemon
import synapse.eventbus as s_eventbus
import synapse.telepath as s_telepath

import synapse.cores.common as s_cores_common

import synapse.lib.tufo as s_tufo
import synapse.lib.scope as s_scope
import synapse.lib.ingest as s_ingest
import synapse.lib.output as s_output
import synapse.lib.msgpack as s_msgpack
import synapse.lib.thishost as s_thishost

from synapse.common import *

from synapse.lib.iq import TstEnv, TstOutPut, SynTest, CmdGenerator

# create the global multi-plexor *not* within a test
# to avoid "leaked resource" when a test triggers creation
s_scope.get('plex')

class TooFewEvents(Exception): pass

TstSSLInvalidClientCertErr = socket.error
TstSSLConnectionResetErr = socket.error

testdir = os.path.dirname(__file__)

def getTestPath(*paths):
    return os.path.join(testdir, *paths)

def checkLock(fd, timeout, wait=0.5):
    wtime = 0

    if timeout < 0:
        raise ValueError('timeout must be > 0')

    while True:
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno == 11:
                return True
        else:
            fcntl.lockf(fd, fcntl.LOCK_UN)
        time.sleep(wait)
        wtime += wait
        if wtime >= timeout:
            return False

def mesg_cmd(query, oper):
    '''
    Test command which adds messages to the storm message queue.

    Args:
        query (s_storm.Query): Query object.
        oper ((str, dict)): Oper tuple

    Returns:
        None
    '''
    query.mesg('Log test messages')
    query.mesg('Query has [%s] nodes' % len(query.data()))

class ModelSeenMixin:

    def check_seen(self, core, node):
        form = node[1]['tufo:form']
        minp = form + ':seen:min'
        maxp = form + ':seen:max'

        self.none(node[1].get(minp))
        self.none(node[1].get(maxp))

        core.setTufoProps(node, **{'seen:min': 100, 'seen:max': 100})
        self.eq(node[1].get(minp), 100)
        self.eq(node[1].get(maxp), 100)

        core.setTufoProps(node, **{'seen:min': 0, 'seen:max': 0})
        self.eq(node[1].get(minp), 0)
        self.eq(node[1].get(maxp), 100)

        core.setTufoProps(node, **{'seen:min': 1000, 'seen:max': 1000})
        self.eq(node[1].get(minp), 0)
        self.eq(node[1].get(maxp), 1000)

class TstMixin:
    '''
    Mixin for test helpers which are not available in SynTest directly
    '''

    @contextlib.contextmanager
    def getSslCore(self, conf=None, configure_roles=False):
        dconf = {'auth:admin': 'root@localhost',
                 'auth:en': 1, }
        if conf:
            conf.update(dconf)
        conf = dconf
        cafile = getTestPath('ca.crt')
        keyfile = getTestPath('server.key')
        certfile = getTestPath('server.crt')
        userkey = getTestPath('user.key')
        usercrt = getTestPath('user.crt')
        rootkey = getTestPath('root.key')
        rootcrt = getTestPath('root.crt')

        amesgs = (
            ('auth:add:user', {'user': 'user@localhost'}),
            ('auth:add:role', {'role': 'creator'}),
            ('auth:add:rrule', {'role': 'creator',
                                'rule': ('node:add',
                                         {'form': '*'})
                                }),
            ('auth:add:rrule', {'role': 'creator',
                                'rule': ('node:tag:add',
                                         {'tag': '*'})
                                }),
            ('auth:add:rrule', {'role': 'creator',
                                'rule': ('node:prop:set',
                                         {'form': '*', 'prop': '*'})
                                }),
            ('auth:add:urole', {'user': 'user@localhost', 'role': 'creator'}),
        )

        with self.getDirCore(conf=conf) as core:
            s_scope.set('syn:core', core)
            with s_daemon.Daemon() as dmon:
                s_scope.set('syn:dmon', dmon)
                dmon.share('core', core)
                link = dmon.listen('ssl://localhost:0/',
                                   cafile=cafile,
                                   keyfile=keyfile,
                                   certfile=certfile,
                                   )
                s_scope.set('syn:test:link', link)
                port = link[1].get('port')
                url = 'ssl://user@localhost/core'
                user_prox = s_telepath.openurl(url,
                                               port=port,
                                               cafile=cafile,
                                               keyfile=userkey,
                                               certfile=usercrt
                                               )  # type: s_cores_common.CoreApi
                root_prox = s_telepath.openurl(url,
                                               port=port,
                                               cafile=cafile,
                                               keyfile=rootkey,
                                               certfile=rootcrt
                                               )  # type: s_cores_common.CoreApi

                if configure_roles:
                    for mesg in amesgs:
                        isok, retn = root_prox.authReact(mesg)
                        s_common.reqok(isok, retn)

                try:
                    yield user_prox, root_prox
                finally:
                    user_prox.fini()
                    root_prox.fini()
