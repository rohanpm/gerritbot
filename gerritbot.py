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



class IrcClient(irclib.SimpleIRCClient):
    pass


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

        for name, channel in self.project_channels.iteritems():
            self.client.connection.join(channel)

    def _topic(self, topic):
        channel = self.config.get(IRC, "channel")
        self.client.connection.topic(channel, topic)

    def change_merged(self, event):
        change = event["change"]

        owner = self.lookup_author(change["owner"]["email"])
        submitter = self.lookup_author(event["submitter"]["email"])

        message = "%s, owned by %s was accepted by %s (%s)" % (change["url"], submitter, owner, change["subject"])
        self.send_message("merge", change["project"], change["branch"], message)


    def comment_added(self, event):
        change = event["change"]

        owner = self.lookup_author(change["owner"]["email"])
        author = self.lookup_author(event["author"]["email"])

        approvals = event["approvals"]
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
                reviewtype += "sanity"
            else:
                reviewtype += "code"

            if approval["type"] == "SRVW" and author == "Qt Sanity Bot":
                has_sanity_plusone = True

            temp = "%s: %s%s" % (reviewtype, approval["value"], color())
            approval_str += temp + " "
            approval_count += 1

        if approval_count == 1 and has_sanity_plusone == True:
            return # no need to spam sanity +1s

        #{"type":"comment-added","change":{"project":"qt/qtjsondb","branch":"master","id":"Id3d738e326ec80da1bcb4f88b04a072ecbc83347","number":"11643","subject":"Added JsonDbClient::generateUuid()","owner":{"name":"Jamey Hicks","email":"jamey.hicks@nokia.com"},"url":"http://codereview.qt-project.org/11643"},"patchSet":{"number":"2","revision":"4f6681f6c13ec27dcfbfdbf36b1e9ceb6ab81be8","ref":"refs/changes/43/11643/2","uploader":{"name":"Jamey Hicks","email":"jamey.hicks@nokia.com"}},"author":{"name":"Qt Sanity Bot","email":"qt_sanity_bot@ovi.com"},"approvals":[{"type":"SRVW","description":"Sanity Review","value":"1"}],"comment":""}
        #{"type":"comment-added","change":{"project":"qt/qtjsondb","branch":"master","id":"Ia111d745f4a57cfe6479d92ed8e0b733f92c4e12","number":"11646","subject":"Added public JsonDbString class.","owner":{"name":"Jamey Hicks","email":"jamey.hicks@nokia.com"},"url":"http://codereview.qt-project.org/11646"},"patchSet":{"number":"3","revision":"93a7673cb03a2b96f1db8e311dc64a48f0d7be08","ref":"refs/changes/46/11646/3","uploader":{"name":"Jamey Hicks","email":"jamey.hicks@nokia.com"}},"author":{"name":"Jeremy Katz","email":"jeremy.katz@nokia.com"},"approvals":[{"type":"CRVW","description":"Code Review","value":"-1"},{"type":"SRVW","description":"Sanity Review","value":"0"}],"comment":"Too Hungarian notation for my taste, with the Str postfix on every public member.\n\nI think JsonDbString::id() is clear enough.\n\nFor the class name, JsonDbString sounds like it\u0027s a subclass of QString. Maybe JsonDbKey?"}

        message = "%s, owned by %s, was commented on by %s: %s" % (change["url"], owner, author, approval_str)
        self.send_message("comment", change["project"], change["branch"], message)

    def patchset_created(self, event):
        change = event["change"]
        #{"type":"patchset-created","change":{"project":"qt/qtbase","branch":"master","id":"Ibffc95833918f65be737f52d694ee81a2036c412","number":"10235","subject":"Fix movablity of QVariant.","owner":{"name":"Jędrzej Nowacki","email":"jedrzej.nowacki@nokia.com"},"url":"http://codereview.qt-project.org/10235"},"patchSet":{"number":"7","revision":"46c54c80327f283e6e95c0c47018c475c71f0443","ref":"refs/changes/35/10235/7","uploader":{"name":"Jędrzej Nowacki","email":"jedrzej.nowacki@nokia.com"}},"uploader":{"name":"Jędrzej Nowacki","email":"jedrzej.nowacki@nokia.com"}}

        owner = self.lookup_author(change["owner"]["email"])

        message = "%s, owned by %s, was submitted (%s)" % (change["url"], owner, change["subject"])
        self.send_message("comment", change["project"], change["branch"], message)







    def lookup_author(self, email_str):
        # special cases
        if email_str == "qt_sanity_bot@ovi.com":
            return "Qt Sanity Bot"
        elif email_str == "qt-info@nokia.com":
            return "Qt CI"

        return re.compile(r'@.+').sub("", email_str)

    def send_message(self, action, project, branch, orig_message):
        print "sending message for " + project
        branch_color = self.branch_colors.get(branch)
        project_channel = self.project_channels.get(project)
        if project_channel == None:
            project = project.replace("qt/", "")
            project_channel = self.config.get(IRC, "channel")
            branch = project + "/" + branch
        print "sending to " + project_channel

        if branch_color != None:
            msg_branch = branch_color + branch + color()
        else:
            msg_branch = branch

        message = "[%s]: %s" % (msg_branch, orig_message)
        self.client.connection.privmsg(project_channel, message)
        pass

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


