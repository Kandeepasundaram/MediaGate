#!/usr/bin/env python3
"""Windows-side notification agent.

Runs a small HTTP server that listens for POST /notify {"title", "body"}
from the Ubuntu backend's cron job and raises a native Windows toast.

Install: pip install winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom
Run manually: python scripts/windows_toast.py
Run as a background task: see scripts/setup_windows.bat (Task Scheduler entry).

This script is Windows-only and is never imported by the Ubuntu backend.
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("windows_toast")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

APP_ID = "MediaManager"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8765


def show_toast(title: str, body: str) -> None:
    try:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastNotificationManager, ToastNotification

        xml = f"""
        <toast>
          <visual>
            <binding template="ToastGeneric">
              <text>{title}</text>
              <text>{body}</text>
            </binding>
          </visual>
        </toast>
        """
        doc = XmlDocument()
        doc.load_xml(xml)
        notifier = ToastNotificationManager.create_toast_notifier(APP_ID)
        notifier.show(ToastNotification(doc))
        logger.info("Toast shown: %s / %s", title, body)
    except ImportError:
        logger.warning(
            "winrt not installed (pip install winrt-Windows.UI.Notifications "
            "winrt-Windows.Data.Xml.Dom); logging notification instead: %s - %s",
            title, body,
        )
    except Exception:
        logger.exception("Failed to show toast notification")


class NotifyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/notify":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        show_toast(payload.get("title", "Media Manager"), payload.get("body", ""))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, fmt: str, *args) -> None:
        logger.info(fmt, *args)


def main() -> None:
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), NotifyHandler)
    logger.info("Windows toast agent listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
