#!/usr/bin/python
# -*- coding: utf-8 -*-


'''
    Copyright 2011, Robin Burchell <robin+qt@viroteck.net>
    Copyright 2010, The Android Open Source Project

    Licensed under the Apache License, Version 2.0 (the "License"); 
    you may not use this file except in compliance with the License. 
    You may obtain a copy of the License at 

        http://www.apache.org/licenses/LICENSE-2.0 

    Unless required by applicable law or agreed to in writing, software 
    distributed under the License is distributed on an "AS IS" BASIS, 
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. 
    See the License for the specific language governing permissions and 
    limitations under the License.
'''

# bridge script to irc channel from gerrit livestream
# written by jeff sharkey and kenny root


import re, os, sys, ConfigParser
import socket, paramiko
import threading, time, random
import simplejson
import irclib
import urllib2


# config file section titles
GERRIT = "GerritServer"
IRC = "IrcServer"
BRANCHES = "Branches"
GENERAL = "General"
PROJECTS = "Projects"

config = ConfigParser.ConfigParser()
config.read("gerritbot.conf")


NONE, BLACK, NAVY, GREEN, RED, BROWN, PURPLE, OLIVE, YELLOW, LIME, TEAL, AQUA, BLUE, PINK, GREY, SILVER, WHITE = range(17)

def color(fg=None, bg=None, bold=False, underline=False):
    # generate sequence for irc formatting
    result = "\x0f"
    if not fg is None: result += "\x03%d" % (fg)
    if not bg is None: result += ",%s" % (bg)
    if bold: result += "\x02"
    if underline: result += "\x1f"
    return result


class GerritThread(threading.Thread):
    def __init__(self, config, irc):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.config = config
        self.irc = irc

    def run(self):
        while True:
            self.run_internal()
            print self, "sleeping and wrapping around"
            time.sleep(5)

    def run_internal(self):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        host = self.config.get(GERRIT, "host")
        port = self.config.getint(GERRIT, "port")
        user = self.config.get(GERRIT, "user")
        privkey = self.config.get(GERRIT, "privkey")

        try:
            print self, "connecting to", host
            client.connect(host, port, user, key_filename=privkey, timeout=60)
            client.get_transport().set_keepalive(60)

            stdin, stdout, stderr = client.exec_command("gerrit stream-events")
            for line in stdout:
                print line
                try:
                    event = simplejson.loads(line)
                    if event["type"] == "comment-added":
                        self.irc.comment_added(event)
                    elif event["type"] == "change-merged":
                        self.irc.change_merged(event)
                    elif event["type"] == "patchset-created":
                        self.irc.patchset_created(event)
                    else:
                        pass
                except ValueError:
                    pass
            client.close()
        except Exception, e:
            print self, "unexpected", e

def getUrl(url):
    print "Loading url %s" % (url)

    try:
        f = urllib2.urlopen(url, None, 5)
    except:
        return None
    return f.read()


class IrcClient(irclib.SimpleIRCClient):
    def on_pubmsg(self, connection, event):
        # source:  w00t!w00t@staff.chatspike.net
        # target: #devel-qtbot
        # arguments: ['ff']
        message = event.arguments()[0]

        # JIRA bugs first
        bugs = re.findall(r"\b(Q[A-Z]+\-[0-9]+)\b", message)

        for bug in bugs:
            json = getUrl("https://bugreports.qt-project.org/rest/api/2/issue/%s" % (bug))
            print "Reporting bug %s to %s" % (bug, event.target())

            if json == None:
                # error
                print "error whilst trying to load bug %s" % (bug)
            else:
                try:
                    ojson = simplejson.loads(json)
                    bugurl = "https://bugreports.qt-project.org/browse/%s" % (bug)
                    connection.privmsg(event.target(),
                            "%s: %s - %s" % (irclib.nm_to_n(event.source()), ojson["fields"]["summary"], bugurl))
                except:
                    print "exception parsing json!"
                    print json
                    connection.privmsg(event.target(), "woah, %s was not a valid bug or something" % (bug))

        # gerrit search: http://codereview.qt-project.org/#q,I,n,z
        # where I is the search thing
        changes = re.findall(r"(I[0-9a-f]{40})", message)

        for change in changes:
            print "Reporting change %s to %s" % (change, event.target())
            connection.privmsg(event.target(), "https://codereview.qt-project.org/#q,%s,n,z" % (change))


class IrcThread(threading.Thread):
    def __init__(self, config):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.config = config

        self.branch_colors = {}
        for name, value in config.items(BRANCHES):
            self.branch_colors[name] = color(globals()[value])

        self.project_channels = {}
        for name, channel in config.items(PROJECTS):
            self.project_channels[name] = channel

    def run(self):
        host = self.config.get(IRC, "host")
        port = self.config.getint(IRC, "port")
        nick = self.config.get(IRC, "nick")

        print self, "connecting to", host
        self.client = IrcClient()
        self.client.connect(host, port, nick, username=nick, ircname=nick)
        self.client.start()

    def finish_setup(self):
        nick = self.config.get(IRC, "nick")
        mode = self.config.get(IRC, "mode")
        channel = self.config.get(IRC, "channel")
        key = self.config.get(IRC, "key")
        nickpass = self.config.get(IRC, "nickpass")

        self.client.connection.privmsg("NickServ", "IDENTIFY %s" % (nickpass))
        self.client.connection.mode(nick, mode)
        time.sleep(2)
        self.client.connection.join(channel, key)
        self.client.connection.join(self.config.get(IRC, "additionalchannels"))

        for name, channel in self.project_channels.iteritems():
            self.client.connection.join(channel)

    def _topic(self, topic):
        channel = self.config.get(IRC, "channel")
        self.client.connection.topic(channel, topic)

    def change_merged(self, event):
        change = event["change"]

        owner = self.lookup_author(change["owner"]["email"])
        submitter = self.lookup_author(event["submitter"]["email"])

        message = "%s from %s staged by %s - %s" % (change["subject"], owner, submitter, change["url"])
        self.send_message("merge", change["project"], change["branch"], message)


    def comment_added(self, event):
        change = event["change"]

        owner = self.lookup_author(change["owner"]["email"])
        author = self.lookup_author(event["author"]["email"])

        approvals = event.get("approvals", [])
        approval_str = ""
        approval_count = 0
        has_sanity_plusone = False

        for approval in approvals:
            if int(approval["value"]) < 0:
                reviewtype = color(RED)
            elif int(approval["value"]) > 0:
                reviewtype = color(GREEN)
            else:
                reviewtype = ""

            if approval["type"] == "SRVW":
                reviewtype += "S"
            else:
                reviewtype += "C"

            if approval["type"] == "SRVW" and author == "Qt Sanity Bot":
                has_sanity_plusone = True

            temp = "%s: %s%s" % (reviewtype, approval["value"], color())
            approval_str += temp + " "
            approval_count += 1

        if approval_count == 1 and has_sanity_plusone == True:
            return # no need to spam sanity +1s

        if author == "Qt CI":
            # special case to detect CI pass/fail
            if event["comment"] == "Successful integration\n\nNo regressions!":
                message = "%s from %s %s_PASSED_%s CI - %s" % (change["subject"], owner, color(GREEN), color(), change["url"])
            else:
                message = "%s from %s %s_FAILED_%s CI - %s" % (change["subject"], owner, color(RED), color(), change["url"])
        else:
            message = "%s from %s reviewed by %s: %s - %s" % (change["subject"], owner, author, approval_str, change["url"])

        self.send_message("comment", change["project"], change["branch"], message)

    def patchset_created(self, event):
        change = event["change"]
        owner = self.lookup_author(change["owner"]["email"])

        if event["patchSet"]["number"] == "1":
            message = "%s pushed by %s - %s" % (change["subject"], owner, change["url"])
        else:
            message = "%s updated to v%s by %s - %s" % (change["subject"], event["patchSet"]["number"], owner, change["url"])

        self.send_message("comment", change["project"], change["branch"], message)







    def lookup_author(self, email_str):
        # special cases
        if email_str == "qt_sanity_bot@ovi.com":
            return "Qt Sanity Bot"
        elif email_str == "ci-noreply@qt-project.org":
            return "Qt CI"

        return re.compile(r'@.+').sub("", email_str)

    def send_message(self, action, project, branch, orig_message):
        print "sending message for " + project
        branch_color = self.branch_colors.get(branch)
        project_channel = self.project_channels.get(project)

        if branch_color != None:
            msg_branch = branch_color + branch + color()
        else:
            msg_branch = branch

        # CC to the generic channel
        message = "[%s]: %s" % (msg_branch, orig_message)
        if project_channel != self.config.get(IRC, "channel"):
            self.client.connection.privmsg(project_channel, message)

        if project_channel == None:
            project = project.replace("qt/", "")
            project_channel = self.config.get(IRC, "channel")
            branch = project + "/" + branch

            if branch_color != None:
                msg_branch = branch_color + branch + color()
            else:
                msg_branch = branch

            message = "[%s]: %s" % (msg_branch, orig_message)
            self.client.connection.privmsg(self.config.get(IRC, "channel"), message)

        # don't flood
        time.sleep(1)



irc = IrcThread(config); irc.start()

# sleep before joining to work around unrealircd bug
time.sleep(2)
irc.finish_setup()

# sleep before spinning up threads to wait for chanserv
time.sleep(5)

gerrit = GerritThread(config, irc); gerrit.start()

while True:
    try:
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        break

