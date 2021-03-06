"""Support module to start a local Akara test server"""

import atexit
import os
from os.path import abspath, dirname
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib2
import urlparse
import httplib

# XXX I only use one function from here. Bring it into this file?
import python_support

######

# Set 'False' to keep the temporary directory used for the server tests
#DELETE_TEMPORARY_SERVER_DIRECTORY = False
DELETE_TEMPORARY_SERVER_DIRECTORY = True
if "AKARA_SAVE" in os.environ:
    DELETE_TEMPORARY_SERVER_DIRECTORY = False


######
ATOM_ENTRIES = os.path.join(dirname(abspath(__file__)), "resource", "atom")
assert os.path.exists(ATOM_ENTRIES), "no atom entries directory?"

# All of the tests use a single server instance.
# This is started the first time it's needed.
# It is stopped during test shutdown.

def _override_server_uri():
    server = os.environ.get("AKARA_TEST_SERVER", None)
    if not server:
        return None
    assert "/" not in server
    return "http://" + server + "/"
    
SERVER_URI = _override_server_uri()
server_root = None
config_filename = None
server_pid = None
server_did_not_start = False

# Create a temporary directory structure for Akara.
# Needs a configuration .ini file and the logs subdirectory.
def create_server_dir(port):
    global server_root, config_filename
    
    server_root = tempfile.mkdtemp(prefix="akara_test_")
    config_filename = os.path.join(server_root, "akara_test.config")

    f = open(config_filename, "w")
    f.write("""
class Akara:
  ServerRoot = %(server_root)r
  ServerPath = 'http://dalkescientific.com/'
  InternalServerPath = 'http://localhost:%(port)s/'
  Listen = 'localhost:%(port)s'
  LogLevel = 'DEBUG'
  MinSpareServers = 3
  # These affect test_server.py:test_restart
  MaxServers = 5
  MaxRequestsPerServer = 5

class atomtools:
  entries = %(atom_entries)r
  feed_envelope = '''<feed xmlns="http://www.w3.org/2005/Atom">
<title>Feed me!</title><id>http://example.com/myfeed</id></feed>'''

class static:
  paths = {
      "resource": %(resource_dir)r,
      "static": %(resource_dir)r + '/static',
      }


# Let the XSLT test reach directly into the filesystem
class xslt:
  uri_space = 'file:///'

MODULES = ["akara.demo.akara_tests",
           "akara.demo.akara_tests",
           "akara.demo.atomtools",
           "akara.demo.calaistools",
           "akara.demo.calweb",
           "akara.demo.collection",
           "akara.demo.echo",
           "akara.demo.icaltools",
           "akara.demo.luckygoogle",
           "akara.demo.markuptools",
           "akara.demo.method_dispatcher",
           "akara.demo.moin2atomentries",
           "akara.demo.moincms",
           "akara.demo.moinrest",
           "akara.demo.oaitools",
           "akara.demo.rdfatools",
           "akara.demo.static",
           "akara.demo.statstools",
           "akara.demo.svntools",
           "akara.demo.unicodetools",
           "akara.demo.wwwlogviewer",
           "akara.demo.xslt",
]           

""" % dict(server_root = server_root,
           port = port,
           atom_entries = os.path.join(ATOM_ENTRIES, "*.atom"),
           resource_dir = os.path.join(dirname(abspath(__file__)), "resource"),
           ))
    f.close()

    os.mkdir(os.path.join(server_root, "logs"))

#FIXME: add back config for:
#[collection]
#folder=/tmp/collection

# Remove the temporary server configuration directory,
# if I created it
def remove_server_dir():
    global server_pid, server_root
    if server_pid is not None:
        # I created the server, I kill it
        os.kill(server_pid, signal.SIGTERM)
        server_pid = None

    if server_root is not None:
        # Very useful when doing development and testing.
        # Would like this as a command-line option somehow.
        if DELETE_TEMPORARY_SERVER_DIRECTORY:
            shutil.rmtree(server_root)
        else:
            print "Test server configuration and log files are in", server_root
        server_root = None

atexit.register(remove_server_dir)

# Start a new Akara server in server mode.
def start_server():
    global server_pid, server_did_not_start
    # There's a PID if the spawning worked
    assert server_pid is None
    # Can only tell if the server starts by doing a request
    # If the request failed, don't try to restart.
    if server_did_not_start:
        raise AssertionError("Already tried once to start the server")

    port = python_support.find_unused_port()
    create_server_dir(port)
    args = ['akara', "--config-file", config_filename, "start"]
    try:
        result = subprocess.call(args)
    except:
        print "Failed to start", args
        raise

    # Akara started, but it might have failed during startup.
    # Report errors by reading the error log
    if result != 0:
        f = open(os.path.join(server_root, "logs", "error.log"))
        err_text = f.read()
        raise AssertionError("Could not start %r:\n%s" % (args, err_text))

    # Akara server started in the background. The main
    # process will only exit with a success (0) if the
    # pid file has been created.
    f = open(os.path.join(server_root, "logs", "akara.pid"))
    line = f.readline()
    f.close()

    # Save the pid information now so the server will be shut down
    # if there are any problems.
    temp_server_pid = int(line)

    # Check to see that the server process really exists.
    # (Is this overkill? Is this portable for Windows?)
    os.kill(temp_server_pid, 0)  # Did Akara really start?

    server_did_not_start = True
    check_that_server_is_available(port)
    server_did_not_start = False

    # Looks like it's legit!
    server_pid = temp_server_pid
    return port

# It takes the server a little while to get started.
# In the worst case (trac #6), top-level import failures
# will loop forever, and the server won't hear requests.
def check_that_server_is_available(port):
    old_timeout = socket.getdefaulttimeout()
    try:
        # How long do you want to wait?
        socket.setdefaulttimeout(20.0)
        try:
            urllib2.urlopen("http://localhost:%d/" % port).read()
        except urllib2.URLError, err:
            print "Current error log is:"
            f = open(os.path.join(server_root, "logs", "error.log"))
            err_text = f.read()
            print err_text
            raise
    finally:
        socket.setdefaulttimeout(old_timeout)

# Get the server URI prefix, like "http://localhost:8880/"
def server():
    global SERVER_URI
    if SERVER_URI is None:
        # No server specified and need to start my own
        port = start_server()
        SERVER_URI = "http://localhost:%d/" % port

    return SERVER_URI

def httplib_server():
    url = server()
    # <scheme>://<netloc>/<path>?<query>#<fragment>
    scheme, netloc, path, query, fragment = urlparse.urlsplit(url)
    assert path in ("", "/"), "Cannot handle path in %r" % url
    assert query == "", "Cannot handle query in %r" % url
    assert fragment == "", "Cannot handle fragment in %r" % url
    assert "@" not in netloc, "Cannot handle '@' in %r" % url
    if ":" in netloc:
        host, port = netloc.split(":")
        port = int(port)
    else:
        host = netloc
        port = 80
    conn = httplib.HTTPConnection(host, port, strict=True)
    return conn
