import argparse
import atexit
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser(description="Serve a directory or file over HTTP")

parser.add_argument("file_or_dir", help="The file or directory to serve over HTTP")
parser.add_argument("-p", "--port", default=8000, type=int, help="The port to serve the content on")
parser.add_argument(
    "-b",
    "--public",
    action="store_true",
    help="Create a publicly availible link through... something",
)

args = parser.parse_args()

def PublicLink():
    # Connect to ProtonVPN-cli
    subprocess.run(["protonvpn-cli", "connect", "--fastest"])
    subprocess.run(["protonvpn-cli", "port-forward", str(args.port)])

    # Extract the IP address from the output
    result = subprocess.run(["protonvpn-cli", "status"], stdout=subprocess.PIPE)
    output = result.stdout.decode("utf-8")

    ip_address = output.split("\n")[0].split(":")[1].strip()

def clean_up():
    share_folder = f"{args.file_or_dir}.fileshare"
    if os.path.isdir(share_folder):
        shutil.rmtree(share_folder)
        print(f"Deleted the content of {share_folder}")
    if args.public:
        subprocess.run(["protonvpn-cli", "disconnect"])

# Get the IP address of the local machine
ip = socket.gethostbyname(socket.gethostname())
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    # Connect to Google's DNS server to get the public IP of the machine
    s.connect(("8.8.8.8", 1))
    ip = s.getsockname()[0]
except:
    pass
finally:
    s.close()

# Register the clean_up function to be called at exit
atexit.register(clean_up)

# Check if the file_or_dir is a file or a directory
if os.path.isfile(args.file_or_dir):
    file_name = os.path.basename(args.file_or_dir)
    share_folder = f"{args.file_or_dir}.fileshare"
    os.makedirs(share_folder, exist_ok=True)
    # Create a symbolic link to the file inside the .fileshare folder
    os.symlink(args.file_or_dir, os.path.join(share_folder, file_name))
    os.chdir(share_folder)
elif os.path.isdir(args.file_or_dir):
    file_name = ""
    os.chdir(args.file_or_dir)
else:
    parser.error(f"{args.file_or_dir} is not a file or directory.")

if args.public:
    ip = PublicLink()

class ForwardAwareHandler(SimpleHTTPRequestHandler):
    server_version = "SolShareHTTP/1.0"
    protocol_version = "HTTP/1.1"
    range = None

    def address_string(self):
        return self.client_address[0]

    def _header(self, name):
        value = self.headers.get(name)
        return value.strip() if value else "-"

    def _forwarded_client(self):
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first

        x_real_ip = self.headers.get("X-Real-IP")
        if x_real_ip:
            return x_real_ip.strip()

        return self.client_address[0]

    def _log_line(self, kind, detail):
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%d/%b/%Y %H:%M:%S %z")
        peer_ip = self.client_address[0]
        effective_ip = self._forwarded_client()
        request_line = getattr(self, "requestline", "-")
        message = (
            f'{peer_ip} effective={effective_ip} '
            f'xff="{self._header("X-Forwarded-For")}" '
            f'xreal="{self._header("X-Real-IP")}" '
            f'forwarded="{self._header("Forwarded")}" '
            f'[{timestamp}] "{request_line}" {kind} {detail}\n'
        )
        sys.stderr.write(message)

    def log_request(self, code="-", size="-"):
        self._log_line("request", f"status={code} size={size}")

    def log_error(self, format, *args):
        self._log_line("error", format % args)

    def _parse_range_header(self, file_size):
        header = self.headers.get("Range")
        if not header:
            return None

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if not match:
            return "invalid"

        start_raw, end_raw = match.groups()
        if not start_raw and not end_raw:
            return "invalid"

        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
            if start >= file_size:
                return "invalid"
        else:
            suffix = int(end_raw)
            if suffix <= 0:
                return "invalid"
            if suffix >= file_size:
                start = 0
            else:
                start = file_size - suffix
            end = file_size - 1

        if end < start:
            return "invalid"

        end = min(end, file_size - 1)
        return start, end

    def send_head(self):
        self.range = None
        path = self.translate_path(self.path)

        if os.path.isdir(path):
            return super().send_head()

        ctype = self.guess_type(path)
        try:
            file_obj = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        try:
            fs = os.fstat(file_obj.fileno())
            file_len = fs.st_size
            range_request = self._parse_range_header(file_len)
            if range_request == "invalid":
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_len}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                file_obj.close()
                return None

            self.send_response(HTTPStatus.PARTIAL_CONTENT if range_request else HTTPStatus.OK)
            self.send_header("Content-type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))

            if range_request:
                start, end = range_request
                self.range = (start, end)
                content_length = end - start + 1
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
            else:
                content_length = file_len

            self.send_header("Content-Length", str(content_length))
            self.end_headers()
            return file_obj
        except Exception:
            file_obj.close()
            raise

    def copyfile(self, source, outputfile):
        try:
            if self.range:
                self._copy_range(source, outputfile, *self.range)
            else:
                self._send_full_file(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            self._log_line("disconnect", "client closed connection during response")

    def _send_full_file(self, source, outputfile):
        try:
            outputfile.flush()
            self.connection.sendfile(source)
            return
        except (AttributeError, OSError, ValueError):
            source.seek(0)
            super().copyfile(source, outputfile)

    def _copy_range(self, source, outputfile, start, end):
        remaining = end - start + 1
        source.seek(start)
        buffer_size = 1024 * 1024

        while remaining > 0:
            chunk = source.read(min(buffer_size, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


# Create an HTTP server to serve the content
httpd = ThreadingHTTPServer(("0.0.0.0", args.port), ForwardAwareHandler)
httpd.daemon_threads = True
print(f"Link: http://{ip}:{args.port}/{file_name}")

try:
    httpd.serve_forever()
except KeyboardInterrupt:
    clean_up()
