#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright 2017 René Moser <mail@renemoser.net>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import re
import os
import ConfigParser

DEBUG = 0


def log(msg):
    sys.stderr.write('backend (%s): %s\n' % (os.getpid(), msg))


def write(*l):
    args = len(l)
    c = 0
    for a in l:
        c += 1
        if DEBUG:
            log('writing: %s' % a)
        sys.stdout.write(a)
        if c < args:
            if DEBUG:
                log('writetab')
            sys.stdout.write('\t')
    if DEBUG:
        log('writenewline')
    sys.stdout.write('\n')
    sys.stdout.flush()


def get_next():
    if DEBUG:
        log('reading now')
    l = sys.stdin.readline()
    if DEBUG:
        log('read line: %s' % l)
    return l.strip().split('\t')


class DynamicBackend:
    def __init__(self):
        self.id = ''
        self.soa = ''
        self.domain = ''
        self.ip_address = ''
        self.ttl = ''
        self.name_servers = {}
        self.additional_cnames = {}
        self.additional_txt = {}

    def configure(self):
        fname = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'backend.conf')
        if not os.path.exists(fname):
            log('%s does not exist' % fname)
            sys.exit(1)

        fp = open(fname)
        config = ConfigParser.ConfigParser()
        config.readfp(fp)
        fp.close()

        self.id = config.get('soa', 'id')
        self.soa = '%s %s %s' % (config.get('soa', 'ns'), config.get('soa', 'hostmaster'), self.id)
        self.domain = config.get('main', 'domain')
        self.ip_address = config.get('main', 'ipaddress')
        self.ttl = config.get('main', 'ttl')

        for entry in config.items('nameservers'):
            if not entry[0].endswith(self.domain):
                name = entry[0] + '.' + self.domain
            else:
                name = entry[0]
            self.name_servers[name] = entry[1]

        for entry in config.items('additional_cnames') or []:
            if not entry[0].endswith(self.domain):
                name = entry[0] + '.' + self.domain
            else:
                name = entry[0]
            self.additional_cnames[name] = entry[1]
            log('add static CNAME: %s = %s' % (entry[0], entry[1]))
            
        for entry in config.items('additional_txt') or []:
            name = entry[0]
            self.additional_txt[name] = entry[1]
            log('add static TXT: %s = %s' % (entry[0], entry[1]))

        log('Name servers: %s' % self.name_servers)
        log('ID: %s' % self.id)
        log('TTL %s' % self.ttl)
        log('SOA: %s' % self.soa)
        log('IP Address: %s' % self.ip_address)
        log('DOMAIN: %s' % self.domain)

    def run(self):
        log('starting up')
        handshake = get_next()
        if handshake[1] != '1':
            log('Not version 1: %s' % handshake)
            sys.exit(1)
        write('OK', 'We are good')
        log('Done handshake')

        while True:
            cmd = get_next()
            if DEBUG:
                log(cmd)

            if len(cmd) < 6:
                log('did not understand: %s' % cmd)
                write('FAIL')
                continue

            qname = cmd[1].lower()
            qtype = cmd[3]

            if qtype in ('TXT', 'CNAME', 'A', 'ANY') and qname.endswith(self.domain):
                if qname == self.domain:
                    self.handle_self(self.domain)
                elif qname in self.name_servers:
                    self.handle_nameservers(qname)
                elif qname in self.additional_cnames:
                    self.handle_additional_cnames(qname)
                elif qname in self.additional_txt:
                    self.handle_additional_txt(qname)
                else:
                    self.handle_subdomains(qname)
            elif qtype == 'SOA' and qname.endswith(self.domain):
                self.handle_soa(qname)
            else:
                self.handle_unknown(qtype, qname)

    def handle_self(self, name):
        write('DATA', name, 'IN', 'A', self.ttl, self.id, self.ip_address)
        self.write_name_servers(name)
        write('END')

    def handle_subdomains(self, qname):
        subdomain = qname[0:qname.find(self.domain) - 1]
        match = re.findall('^(?:.+\.)?(\d{1,3}[-.]\d{1,3}[-.]\d{1,3}[-.]\d{1,3})$', subdomain)

        if not match:
            if DEBUG:
                log('%s is invalid format' % subdomain)
            self.handle_self(qname)
            return

        ipaddress = re.split('[-.]', match[0])
        if DEBUG:
            log('ip: %s' % ipaddress)
        for part in ipaddress:
            if re.match('^\d{1,3}$', part) is None:
                if DEBUG:
                    log('%s is not a number' % part)
                self.handle_self(qname)
                return
            parti = int(part)
            if parti < 0 or parti > 255:
                if DEBUG:
                    log('%d is too big/small' % parti)
                self.handle_self(qname)
                return

        write('DATA', qname, 'IN', 'A', self.ttl, self.id, '%s.%s.%s.%s' % (ipaddress[0], ipaddress[1], ipaddress[2], ipaddress[3]))
        self.write_name_servers(qname)
        write('END')

    def handle_additional_cnames(self, qname):
        if DEBUG:
            log('Found CNAME: %s' % qname)
        cname=self.additional_cnames[qname]
        write('DATA', qname, 'IN', 'CNAME', self.ttl, self.id, cname)
        write('END')
        
    def handle_additional_txt(self, qname):
        if DEBUG:
            log('Found TXT: %s' % qname)
        txt=self.additional_txt[qname]
        write('DATA', qname, 'IN', 'TXT', self.ttl, self.id, txt)
        write('END')

    def handle_nameservers(self, qname):
        if DEBUG:
            log('Found name server: %s' % qname)
        ip = self.name_servers[qname]
        write('DATA', qname, 'IN', 'A', self.ttl, self.id, ip)
        write('END')

    def write_name_servers(self, qname):
        for nameServer in self.name_servers:
            write('DATA', qname, 'IN', 'NS', self.ttl, self.id, nameServer)

    def handle_soa(self, qname):
        write('DATA', qname, 'IN', 'SOA', self.ttl, self.id, self.soa)
        write('END')

    def handle_unknown(self, qtype, qname):
        write('LOG', 'Unknown type: %s, domain: %s' % (qtype, qname))
        write('END')


if __name__ == '__main__':
    backend = DynamicBackend()
    backend.configure()
    backend.run()
