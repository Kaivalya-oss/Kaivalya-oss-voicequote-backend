"""A throwaway mail server for the isolated test network. Accepts everything, sends nothing onward.

SMTP on 2525. GET /message?to=<address> on 8025 returns the newest message for that address.
"""
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

messages = {}


class Smtp(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.reply("220 sink")
        recipient = None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode(errors="replace").strip()
            upper = command.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                self.reply("250 sink")
            elif upper.startswith("RCPT TO"):
                recipient = command.split("<")[1].split(">")[0].lower()
                self.reply("250 ok")
            elif upper == "DATA":
                self.reply("354 go")
                body = []
                while True:
                    data = self.rfile.readline().decode(errors="replace")
                    if data.strip() == ".":
                        break
                    body.append(data)
                messages[recipient] = "".join(body)
                self.reply("250 stored")
            elif upper == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class Http(BaseHTTPRequestHandler):
    def do_GET(self):
        address = parse_qs(urlparse(self.path).query).get("to", [""])[0].lower()
        found = messages.get(address)
        self.send_response(200 if found else 404)
        self.end_headers()
        self.wfile.write((found or "").encode())

    def log_message(self, *_):
        pass


class Threaded(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


threading.Thread(target=Threaded(("0.0.0.0", 2525), Smtp).serve_forever, daemon=True).start()
HTTPServer(("0.0.0.0", 8025), Http).serve_forever()
