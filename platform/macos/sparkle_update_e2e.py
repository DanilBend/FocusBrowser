#!/usr/bin/env python3
"""Isolated end-to-end acceptance for Focus Browser's pinned Sparkle runtime.

The test builds two tiny synthetic app bundles in an owner-private temporary
directory, serves a signed feed and archive from loopback, and lets the real
pinned Sparkle.framework replace version 1.0.5.0 with 1.0.6.0.  It never uses
the production signing key, the login Keychain, a real application install, or
the user's normal preferences/cache directories.
"""

import argparse
import base64
import binascii
import hashlib
import http.server
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import acquire_sparkle


SCHEMA = 1
OLD_VERSION = "1.0.5.0"
OLD_SHORT_VERSION = "1.0.5"
NEXT_VERSION = "1.0.6.0"
NEXT_SHORT_VERSION = "1.0.6"
APP_NAME = "Focus Sparkle E2E.app"
APP_EXECUTABLE = "Focus Sparkle E2E"
BUNDLE_ID = "com.focusbrowser.sparkle-e2e"
ARCHIVE_NAME = "FocusBrowser-Sparkle-E2E.zip"
APPCAST_NAME = "appcast.xml"
MARKER_NAME = "sparkle-e2e-relaunched.json"
EVENTS_NAME = "sparkle-e2e-events.log"
FAILURE_NAME = "sparkle-e2e-failure.txt"
NEXT_PAYLOAD_NAME = "next-version-payload.txt"
TIMEOUT_SECONDS = 120
MAX_HTTP_REQUESTS = 16
RELEASE_CHALLENGE_RE = re.compile(r"[0-9a-f]{64}")

DITTO = "/usr/bin/ditto"
CODESIGN = "/usr/bin/codesign"
SWIFT = "/usr/bin/swift"
XCRUN = "/usr/bin/xcrun"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UPDATER_PATCH = (
    REPOSITORY_ROOT / "platform/macos/patches/focus-sparkle-autoupdate.patch"
)


class SparkleE2EError(RuntimeError):
    """Raised when the isolated Sparkle update cannot be proven."""


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command, *, env=None, timeout=TIMEOUT_SECONDS, check=True):
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise SparkleE2EError("command must be a non-empty string list")
    clean_env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    if env is not None:
        clean_env.update(env)
    try:
        result = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=clean_env,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SparkleE2EError(
            "command could not run: {}".format(command[0])
        ) from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SparkleE2EError(
            "command failed ({}): {}: {}".format(
                result.returncode, command[0], detail or "no diagnostic output"
            )
        )
    return result


def _resolve_developer_dir(value):
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise SparkleE2EError("developer directory must not be a symlink")
    try:
        root = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SparkleE2EError("developer directory does not exist") from exc
    if (
        not root.is_dir()
        or not (root / "Toolchains/XcodeDefault.xctoolchain/usr/bin/clang").is_file()
        or not (root / "Platforms/MacOSX.platform/Developer/SDKs").is_dir()
    ):
        raise SparkleE2EError("developer directory is not a complete Xcode toolchain")
    return root


def _xcrun(developer_dir, *arguments):
    result = _run(
        [XCRUN, *arguments],
        env={"DEVELOPER_DIR": str(developer_dir)},
    )
    value = result.stdout.decode("utf-8", errors="strict").strip()
    if not value:
        raise SparkleE2EError("xcrun returned an empty tool path")
    return value


def _ephemeral_ed25519_keypair(temp_root):
    # CryptoKit creates a fresh 32-byte seed and public key. The seed is only
    # written to the 0700 test root, passed by pathname to sign_update, and
    # destroyed with that root. No login-Keychain operation is performed.
    source = (
        "import CryptoKit; import Foundation; "
        "let key = Curve25519.Signing.PrivateKey(); "
        "print(key.rawRepresentation.base64EncodedString()); "
        "print(key.publicKey.rawRepresentation.base64EncodedString())"
    )
    result = _run([SWIFT, "-e", source], timeout=60)
    lines = result.stdout.decode("utf-8", errors="strict").splitlines()
    if len(lines) != 2:
        raise SparkleE2EError("CryptoKit did not return one private/public keypair")
    try:
        private = base64.b64decode(lines[0], validate=True)
        public = base64.b64decode(lines[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SparkleE2EError("CryptoKit returned malformed base64") from exc
    if len(private) != 32 or len(public) != 32:
        raise SparkleE2EError("CryptoKit returned an invalid Ed25519 key length")
    key_file = temp_root / "ephemeral-test-ed25519.seed"
    descriptor = os.open(
        str(key_file),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        stream.write(lines[0].encode("ascii") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return key_file, lines[1]


def _write_plist(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        plistlib.dump(value, stream, fmt=plistlib.FMT_BINARY, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o644)


HOST_SOURCE = r'''
#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

static NSString* TestRoot(void) {
  return [[[NSBundle mainBundle] bundlePath] stringByDeletingLastPathComponent];
}

static void AppendEvent(NSString* value) {
  NSString* path = [TestRoot() stringByAppendingPathComponent:@"sparkle-e2e-events.log"];
  int descriptor = open(path.fileSystemRepresentation,
                        O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW, 0600);
  if (descriptor < 0) abort();
  NSData* line = [[value stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
  if (write(descriptor, line.bytes, line.length) != (ssize_t)line.length) abort();
  if (fsync(descriptor) != 0 || close(descriptor) != 0) abort();
}

static void RecordFailure(NSError* error) {
  NSString* value = [NSString stringWithFormat:@"%ld:%@", (long)error.code,
                                                   error.localizedDescription];
  NSString* path = [TestRoot() stringByAppendingPathComponent:@"sparkle-e2e-failure.txt"];
  [value writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil];
  chmod(path.fileSystemRepresentation, 0600);
  AppendEvent([@"failure:" stringByAppendingString:value]);
}

@interface FocusE2EUserDriver : NSObject <SPUUserDriver>
@end

@implementation FocusE2EUserDriver
- (void)showUpdatePermissionRequest:(SPUUpdatePermissionRequest*)request
                              reply:(void (^)(SUUpdatePermissionResponse*))reply {
  AppendEvent(@"permission-request");
  reply([[SUUpdatePermissionResponse alloc]
      initWithAutomaticUpdateChecks:YES sendSystemProfile:NO]);
}
- (void)showUserInitiatedUpdateCheckWithCancellation:(void (^)(void))cancellation {
  AppendEvent(@"feed-request-started");
}
- (void)showUpdateFoundWithAppcastItem:(SUAppcastItem*)item
                                 state:(SPUUserUpdateState*)state
                                 reply:(void (^)(SPUUserUpdateChoice))reply {
  AppendEvent([@"update-found:" stringByAppendingString:item.versionString]);
  reply(SPUUserUpdateChoiceInstall);
}
- (void)showUpdateReleaseNotesWithDownloadData:(SPUDownloadData*)data {}
- (void)showUpdateReleaseNotesFailedToDownloadWithError:(NSError*)error {}
- (void)showUpdateNotFoundWithError:(NSError*)error
                    acknowledgement:(void (^)(void))acknowledgement {
  RecordFailure(error); acknowledgement(); [NSApp terminate:nil];
}
- (void)showUpdaterError:(NSError*)error acknowledgement:(void (^)(void))acknowledgement {
  RecordFailure(error); acknowledgement(); [NSApp terminate:nil];
}
- (void)showDownloadInitiatedWithCancellation:(void (^)(void))cancellation {
  AppendEvent(@"download-started");
}
- (void)showDownloadDidReceiveExpectedContentLength:(uint64_t)length {
  AppendEvent(@"download-length-received");
}
- (void)showDownloadDidReceiveDataOfLength:(uint64_t)length {}
- (void)showDownloadDidStartExtractingUpdate { AppendEvent(@"extract-started"); }
- (void)showExtractionReceivedProgress:(double)progress {}
- (void)showReadyToInstallAndRelaunch:(void (^)(SPUUserUpdateChoice))reply {
  AppendEvent(@"ready-to-install"); reply(SPUUserUpdateChoiceInstall);
}
- (void)showInstallingUpdateWithApplicationTerminated:(BOOL)terminated
                            retryTerminatingApplication:(void (^)(void))retry {
  AppendEvent(terminated ? @"installing-after-termination" : @"installing");
}
- (void)showUpdateInstalledAndRelaunched:(BOOL)relaunched
                         acknowledgement:(void (^)(void))acknowledgement {
  AppendEvent(relaunched ? @"installed-and-relaunched" : @"installed");
  acknowledgement();
}
- (void)dismissUpdateInstallation { AppendEvent(@"installation-dismissed"); }
@end

@interface FocusE2EDelegate : NSObject <NSApplicationDelegate, SPUUpdaterDelegate>
@end

@implementation FocusE2EDelegate {
  FocusE2EUserDriver* _driver;
  SPUUpdater* _updater;
}
- (void)applicationDidFinishLaunching:(NSNotification*)notification {
  NSString* version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleVersion"];
  AppendEvent([@"launched:" stringByAppendingString:version]);
  if ([version isEqualToString:@"1.0.6.0"]) {
    NSString* marker = [TestRoot() stringByAppendingPathComponent:@"sparkle-e2e-relaunched.json"];
    NSDictionary* value = @{ @"passed": @YES, @"version": version };
    NSData* data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    if (![data writeToFile:marker options:NSDataWritingAtomic error:nil]) abort();
    chmod(marker.fileSystemRepresentation, 0600);
    AppendEvent(@"relaunch-next-version");
    [NSApp terminate:nil];
    return;
  }
  if (![version isEqualToString:@"1.0.5.0"]) abort();
  _driver = [[FocusE2EUserDriver alloc] init];
  _updater = [[SPUUpdater alloc] initWithHostBundle:[NSBundle mainBundle]
                                  applicationBundle:[NSBundle mainBundle]
                                          userDriver:_driver
                                            delegate:self];
  NSError* error = nil;
  if (![_updater startUpdater:&error]) {
    RecordFailure(error); [NSApp terminate:nil]; return;
  }
  AppendEvent(@"updater-started");
  [_updater checkForUpdates];
}
- (void)updater:(SPUUpdater*)updater didFinishLoadingAppcast:(SUAppcast*)appcast {
  AppendEvent(@"feed-loaded");
}
- (void)updater:(SPUUpdater*)updater didFindValidUpdate:(SUAppcastItem*)item {
  AppendEvent([@"valid-update:" stringByAppendingString:item.versionString]);
}
- (void)updater:(SPUUpdater*)updater willDownloadUpdate:(SUAppcastItem*)item
    withRequest:(NSMutableURLRequest*)request { AppendEvent(@"will-download"); }
- (void)updater:(SPUUpdater*)updater didDownloadUpdate:(SUAppcastItem*)item {
  AppendEvent(@"did-download");
}
- (void)updater:(SPUUpdater*)updater willExtractUpdate:(SUAppcastItem*)item {
  AppendEvent(@"will-extract");
}
- (void)updater:(SPUUpdater*)updater didExtractUpdate:(SUAppcastItem*)item {
  AppendEvent(@"did-extract");
}
- (void)updater:(SPUUpdater*)updater willInstallUpdate:(SUAppcastItem*)item {
  AppendEvent(@"will-install");
}
- (BOOL)updaterShouldRelaunchApplication:(SPUUpdater*)updater { return YES; }
- (void)updaterWillRelaunchApplication:(SPUUpdater*)updater {
  AppendEvent(@"will-relaunch");
}
- (void)updater:(SPUUpdater*)updater didAbortWithError:(NSError*)error {
  RecordFailure(error);
}
- (void)updater:(SPUUpdater*)updater
    didFinishUpdateCycleForUpdateCheck:(SPUUpdateCheck)check
                                 error:(NSError*)error {
  if (error != nil) RecordFailure(error);
}
@end

int main(int argc, const char* argv[]) {
  @autoreleasepool {
    NSApplication* application = [NSApplication sharedApplication];
    FocusE2EDelegate* delegate = [[FocusE2EDelegate alloc] init];
    application.delegate = delegate;
    [application run];
  }
  return 0;
}
'''


def _bundle_plist(version, short_version, feed_url, public_key):
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": APP_EXECUTABLE,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Focus Sparkle E2E",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": short_version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSPrincipalClass": "NSApplication",
        "SUAllowsAutomaticUpdates": True,
        "SUAutomaticallyUpdate": True,
        "SUEnableAutomaticChecks": True,
        "SUEnableJavaScript": False,
        "SUEnableSystemProfiling": False,
        "SUFeedURL": feed_url,
        "SUPublicEDKey": public_key,
        "SURequireSignedFeed": True,
        "SUSignedFeedFailureExpirationInterval": 0,
        "SUVerifyUpdateBeforeExtraction": True,
    }


def _build_bundle(
    root, framework, clang, sdk_path, developer_dir, feed_url, public_key
):
    app = root / APP_NAME
    executable = app / "Contents/MacOS" / APP_EXECUTABLE
    executable.parent.mkdir(parents=True)
    source = root / "FocusSparkleE2E.m"
    source.write_text(HOST_SOURCE, encoding="utf-8")
    source.chmod(0o600)
    _run(
        [
            clang,
            "-fobjc-arc",
            "-fblocks",
            "-mmacosx-version-min=12.0",
            "-isysroot",
            sdk_path,
            "-F",
            str(framework.parent),
            "-framework",
            "AppKit",
            "-framework",
            "Sparkle",
            "-Wl,-rpath,@executable_path/../Frameworks",
            str(source),
            "-o",
            str(executable),
        ],
        env={"DEVELOPER_DIR": str(developer_dir)},
    )
    executable.chmod(0o755)
    embedded = app / "Contents/Frameworks/Sparkle.framework"
    embedded.parent.mkdir(parents=True)
    _run([DITTO, str(framework), str(embedded)])
    _write_plist(
        app / "Contents/Info.plist",
        _bundle_plist(OLD_VERSION, OLD_SHORT_VERSION, feed_url, public_key),
    )
    (app / "Contents/PkgInfo").write_bytes(b"APPL????")
    (app / "Contents/PkgInfo").chmod(0o644)
    _run([CODESIGN, "--force", "--sign", "-", "--timestamp=none", str(app)])
    _run([CODESIGN, "--verify", "--deep", "--strict", str(app)])
    return app


def _make_next_bundle(old_app, server_root, feed_url, public_key):
    next_app = server_root / APP_NAME
    _run([DITTO, str(old_app), str(next_app)])
    info = next_app / "Contents/Info.plist"
    info.unlink()
    _write_plist(
        info,
        _bundle_plist(NEXT_VERSION, NEXT_SHORT_VERSION, feed_url, public_key),
    )
    payload = next_app / "Contents/Resources" / NEXT_PAYLOAD_NAME
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"Focus Sparkle isolated next-version payload\n")
    payload.chmod(0o644)
    _run([CODESIGN, "--force", "--sign", "-", "--timestamp=none", str(next_app)])
    _run([CODESIGN, "--verify", "--deep", "--strict", str(next_app)])
    archive = server_root / ARCHIVE_NAME
    _run(
        [
            DITTO,
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(next_app),
            str(archive),
        ]
    )
    return next_app, archive, _sha256(payload)


def _archive_signature(sign_update, key_file, archive):
    result = _run(
        [str(sign_update), "--ed-key-file", str(key_file), str(archive)]
    )
    output = result.stdout.decode("utf-8", errors="strict").strip()
    match = re.fullmatch(
        r'sparkle:edSignature="([A-Za-z0-9+/]+={0,2})" length="([0-9]+)"',
        output,
    )
    if match is None or int(match.group(2)) != archive.stat().st_size:
        raise SparkleE2EError("sign_update returned malformed archive metadata")
    if len(base64.b64decode(match.group(1), validate=True)) != 64:
        raise SparkleE2EError("sign_update returned an invalid Ed25519 signature")
    return match.group(1)


def _write_signed_feed(server_root, port, signature, archive, sign_update, key_file):
    feed = server_root / APPCAST_NAME
    value = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Focus Browser isolated Sparkle acceptance</title>
    <item>
      <title>Focus Browser {short}</title>
      <sparkle:version>{version}</sparkle:version>
      <sparkle:shortVersionString>{short}</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion>
      <enclosure url="http://127.0.0.1:{port}/{archive}" length="{length}" type="application/octet-stream" sparkle:edSignature="{signature}" />
    </item>
  </channel>
</rss>
""".format(
        short=NEXT_SHORT_VERSION,
        version=NEXT_VERSION,
        port=port,
        archive=ARCHIVE_NAME,
        length=archive.stat().st_size,
        signature=signature,
    )
    feed.write_text(value, encoding="utf-8")
    feed.chmod(0o600)
    _run([str(sign_update), "--ed-key-file", str(key_file), str(feed)])
    if b"sparkle-signatures:" not in feed.read_bytes():
        raise SparkleE2EError("signed appcast omitted its Ed25519 signature trailer")
    return feed


class _LoopbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, server_root):
        self.server_root = Path(server_root)
        self.requests = []
        super().__init__(("127.0.0.1", 0), _LoopbackHandler)


class _LoopbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FocusSparkleE2E/1"
    protocol_version = "HTTP/1.0"

    def log_message(self, _format, *_args):
        return

    def do_HEAD(self):
        self._serve(send_body=False)

    def do_GET(self):
        self._serve(send_body=True)

    def _serve(self, send_body):
        peer = self.client_address[0]
        parsed = urllib.parse.urlsplit(self.path)
        allowed = {
            "/" + APPCAST_NAME: APPCAST_NAME,
            "/" + ARCHIVE_NAME: ARCHIVE_NAME,
        }
        status = 404
        sent = 0
        if (
            peer == "127.0.0.1"
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path in allowed
            and len(self.server.requests) < MAX_HTTP_REQUESTS
        ):
            target = self.server.server_root / allowed[parsed.path]
            if target.is_file() and not target.is_symlink():
                body = target.read_bytes()
                status = 200
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
                    sent = len(body)
        if status != 200:
            self.send_error(status)
        self.server.requests.append(
            {
                "method": self.command,
                "path": parsed.path,
                "peer": peer,
                "status": status,
                "bytes": sent,
            }
        )


def _wait_for_marker(process, marker, failure, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file():
            return
        if failure.is_file():
            detail = failure.read_text(encoding="utf-8", errors="replace")
            raise SparkleE2EError("Sparkle host reported failure: {}".format(detail))
        if process.poll() is not None and process.returncode not in (0, None):
            raise SparkleE2EError(
                "synthetic host exited with status {}".format(process.returncode)
            )
        time.sleep(0.1)
    raise SparkleE2EError("timed out waiting for the replaced app to relaunch")


def run_e2e(
    sparkle_source_root,
    developer_dir,
    timeout=TIMEOUT_SECONDS,
    release_challenge=None,
):
    if platform.system() != "Darwin":
        raise SparkleE2EError("Sparkle end-to-end acceptance is macOS-only")
    if timeout < 30 or timeout > 600:
        raise SparkleE2EError("timeout must be between 30 and 600 seconds")
    if release_challenge is not None and RELEASE_CHALLENGE_RE.fullmatch(
        release_challenge
    ) is None:
        raise SparkleE2EError("release challenge must be 64 lowercase hex characters")
    try:
        dependency = acquire_sparkle.validate_dependency_root(sparkle_source_root)
    except acquire_sparkle.SparkleAcquisitionError as exc:
        raise SparkleE2EError("pinned Sparkle dependency is invalid: {}".format(exc)) from exc
    sparkle_root = Path(dependency["root"])
    framework = sparkle_root / "Sparkle.framework"
    sign_update = sparkle_root / "bin/sign_update"
    developer = _resolve_developer_dir(developer_dir)
    clang = _xcrun(developer, "--sdk", "macosx", "--find", "clang")
    sdk_path = _xcrun(developer, "--sdk", "macosx", "--show-sdk-path")

    with tempfile.TemporaryDirectory(prefix="focus-sparkle-e2e-") as raw_root:
        root = Path(raw_root).resolve()
        os.chmod(root, 0o700)
        install_root = root / "install"
        server_root = root / "server"
        private_home = root / "home"
        for directory in (install_root, server_root, private_home):
            directory.mkdir(mode=0o700)
        server = _LoopbackServer(server_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        if server.server_address[0] != "127.0.0.1":
            raise SparkleE2EError("test server escaped the loopback interface")
        feed_url = "http://127.0.0.1:{}/{}".format(port, APPCAST_NAME)
        key_file, public_key = _ephemeral_ed25519_keypair(root)
        old_app = _build_bundle(
            install_root,
            framework,
            clang,
            sdk_path,
            developer,
            feed_url,
            public_key,
        )
        next_app, archive, next_payload_sha256 = _make_next_bundle(
            old_app, server_root, feed_url, public_key
        )
        signature = _archive_signature(sign_update, key_file, archive)
        feed = _write_signed_feed(
            server_root, port, signature, archive, sign_update, key_file
        )
        key_file.unlink()

        marker = install_root / MARKER_NAME
        failure = install_root / FAILURE_NAME
        events_path = install_root / EVENTS_NAME
        executable = old_app / "Contents/MacOS" / APP_EXECUTABLE
        environment = {
            "DEVELOPER_DIR": str(developer),
            "HOME": str(private_home),
            "CFFIXED_USER_HOME": str(private_home),
            "TMPDIR": str(root / "tmp"),
        }
        (root / "tmp").mkdir(mode=0o700)
        process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C",
                "LC_ALL": "C",
                **environment,
            },
            close_fds=True,
            start_new_session=True,
        )
        try:
            _wait_for_marker(process, marker, failure, timeout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        with (old_app / "Contents/Info.plist").open("rb") as stream:
            installed_info = plistlib.load(stream)
        installed_payload = old_app / "Contents/Resources" / NEXT_PAYLOAD_NAME
        if installed_info.get("CFBundleVersion") != NEXT_VERSION:
            raise SparkleE2EError("Sparkle did not replace the on-disk bundle version")
        if not installed_payload.is_file() or installed_payload.is_symlink():
            raise SparkleE2EError("Sparkle did not install the next-version payload")
        if _sha256(installed_payload) != next_payload_sha256:
            raise SparkleE2EError("installed next-version payload differs from archive")
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
        if marker_value != {"passed": True, "version": NEXT_VERSION}:
            raise SparkleE2EError("relaunch marker is invalid")
        events = events_path.read_text(encoding="utf-8").splitlines()
        required_events = {
            "updater-started",
            "feed-loaded",
            "valid-update:" + NEXT_VERSION,
            "will-download",
            "did-download",
            "will-extract",
            "did-extract",
            "will-install",
            "relaunch-next-version",
        }
        missing_events = required_events - set(events)
        if missing_events:
            raise SparkleE2EError(
                "Sparkle event evidence is incomplete: {}".format(
                    ", ".join(sorted(missing_events))
                )
            )
        feed_requests = [
            item
            for item in server.requests
            if item["method"] == "GET"
            and item["path"] == "/" + APPCAST_NAME
            and item["status"] == 200
        ]
        archive_requests = [
            item
            for item in server.requests
            if item["method"] == "GET"
            and item["path"] == "/" + ARCHIVE_NAME
            and item["status"] == 200
            and item["bytes"] == archive.stat().st_size
        ]
        if len(feed_requests) != 1 or len(archive_requests) != 1:
            raise SparkleE2EError("loopback feed/download request evidence is incomplete")
        if any(item["peer"] != "127.0.0.1" for item in server.requests):
            raise SparkleE2EError("test server accepted a non-loopback peer")

        report = {
            "schema": SCHEMA,
            "passed": True,
            "test": "isolated-full-sparkle-update",
            "old_version": OLD_VERSION,
            "next_version": NEXT_VERSION,
            "version_namespace": "CFBundleVersion/sparkle:version",
            "sparkle_version": acquire_sparkle.SPARKLE_VERSION,
            "sparkle_framework_subtree_sha256": dependency[
                "framework_subtree_sha256"
            ],
            "sparkle_dependency_receipt_sha256": dependency["receipt_sha256"],
            "updater_patch_sha256": _sha256(UPDATER_PATCH),
            "harness_sha256": _sha256(Path(__file__)),
            "release_challenge": release_challenge,
            "architecture": platform.machine(),
            "feed_transport": "loopback-http-only",
            "feed_request_verified": True,
            "archive_download_verified": True,
            "eddsa_archive_verified_by_sparkle": True,
            "signed_feed_verified_by_sparkle": True,
            "bundle_replacement_verified": True,
            "relaunch_verified": True,
            "user_profile_isolated": True,
            "keychain_private_key_used": False,
            "production_private_key_used": False,
            "real_application_install_used": False,
            "public_network_used": False,
            "archive": {
                "bytes": archive.stat().st_size,
                "sha256": _sha256(archive),
            },
            "appcast_sha256": _sha256(feed),
            "event_sequence": events,
            "http_requests": server.requests,
        }
        return report


def _is_lower_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_report(
    report,
    expected_patch_sha256=None,
    expected_release_challenge=None,
):
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise SparkleE2EError("Sparkle E2E report schema mismatch")
    exact_true = (
        "passed",
        "feed_request_verified",
        "archive_download_verified",
        "eddsa_archive_verified_by_sparkle",
        "signed_feed_verified_by_sparkle",
        "bundle_replacement_verified",
        "relaunch_verified",
        "user_profile_isolated",
    )
    if any(report.get(key) is not True for key in exact_true):
        raise SparkleE2EError("Sparkle E2E report is incomplete")
    exact_false = (
        "keychain_private_key_used",
        "production_private_key_used",
        "real_application_install_used",
        "public_network_used",
    )
    if any(report.get(key) is not False for key in exact_false):
        raise SparkleE2EError("Sparkle E2E isolation contract is incomplete")
    if (
        report.get("test") != "isolated-full-sparkle-update"
        or report.get("old_version") != OLD_VERSION
        or report.get("next_version") != NEXT_VERSION
        or report.get("version_namespace") != "CFBundleVersion/sparkle:version"
        or report.get("sparkle_version") != acquire_sparkle.SPARKLE_VERSION
        or report.get("sparkle_framework_subtree_sha256")
        != acquire_sparkle.EXPECTED_FRAMEWORK_SUBTREE_SHA256
        or report.get("feed_transport") != "loopback-http-only"
        or report.get("architecture") not in ("arm64", "x86_64")
    ):
        raise SparkleE2EError("Sparkle E2E identity contract mismatch")
    if expected_release_challenge is not None:
        if (
            not isinstance(expected_release_challenge, str)
            or RELEASE_CHALLENGE_RE.fullmatch(expected_release_challenge) is None
            or report.get("release_challenge") != expected_release_challenge
        ):
            raise SparkleE2EError("Sparkle E2E report does not bind this release run")
    patch_sha256 = expected_patch_sha256 or _sha256(UPDATER_PATCH)
    if report.get("updater_patch_sha256") != patch_sha256:
        raise SparkleE2EError("Sparkle E2E report does not bind the updater patch")
    if report.get("harness_sha256") != _sha256(Path(__file__)):
        raise SparkleE2EError("Sparkle E2E report does not bind the harness")
    if not _is_lower_sha256(report.get("sparkle_dependency_receipt_sha256")):
        raise SparkleE2EError("Sparkle E2E dependency receipt hash is invalid")
    archive = report.get("archive")
    if (
        not isinstance(archive, dict)
        or not isinstance(archive.get("bytes"), int)
        or isinstance(archive.get("bytes"), bool)
        or archive.get("bytes") <= 0
        or not _is_lower_sha256(archive.get("sha256"))
        or not _is_lower_sha256(report.get("appcast_sha256"))
    ):
        raise SparkleE2EError("Sparkle E2E payload evidence is invalid")
    events = report.get("event_sequence")
    required_events = {
        "updater-started",
        "feed-request-started",
        "feed-loaded",
        "valid-update:" + NEXT_VERSION,
        "update-found:" + NEXT_VERSION,
        "will-download",
        "download-started",
        "did-download",
        "will-extract",
        "extract-started",
        "did-extract",
        "ready-to-install",
        "will-install",
        "will-relaunch",
        "relaunch-next-version",
    }
    if (
        not isinstance(events, list)
        or not all(isinstance(event, str) and event for event in events)
        or not required_events.issubset(events)
        or not events
        or events[0] != "launched:" + OLD_VERSION
        or events[-1] != "relaunch-next-version"
        or "launched:" + NEXT_VERSION not in events
    ):
        raise SparkleE2EError("Sparkle E2E event evidence is incomplete")
    requests = report.get("http_requests")
    expected_requests = {
        ("GET", "/" + APPCAST_NAME),
        ("GET", "/" + ARCHIVE_NAME),
    }
    if (
        not isinstance(requests, list)
        or len(requests) != 2
        or any(
            not isinstance(item, dict)
            or item.get("peer") != "127.0.0.1"
            or item.get("status") != 200
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or item.get("bytes") <= 0
            for item in requests
        )
        or {(item.get("method"), item.get("path")) for item in requests}
        != expected_requests
        or next(
            item["bytes"]
            for item in requests
            if item["path"] == "/" + ARCHIVE_NAME
        )
        != archive["bytes"]
    ):
        raise SparkleE2EError("Sparkle E2E report omitted request evidence")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparkle-source-root", required=True)
    parser.add_argument(
        "--developer-dir",
        default=os.environ.get("DEVELOPER_DIR", ""),
        help="Xcode Contents/Developer; defaults to DEVELOPER_DIR",
    )
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument(
        "--release-challenge",
        help="64 lowercase hex characters; required by the public release gate",
    )
    parser.add_argument("--output")
    arguments = parser.parse_args(argv)
    if not arguments.developer_dir:
        print("Sparkle E2E failed: --developer-dir is required", file=sys.stderr)
        return 2
    try:
        report = run_e2e(
            arguments.sparkle_source_root,
            arguments.developer_dir,
            timeout=arguments.timeout,
            release_challenge=arguments.release_challenge,
        )
        validate_report(
            report,
            expected_release_challenge=arguments.release_challenge,
        )
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if arguments.output:
            output = Path(arguments.output).expanduser()
            descriptor = os.open(
                str(output),
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(encoded.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        print(encoded, end="")
        return 0
    except (OSError, SparkleE2EError) as exc:
        print("Sparkle E2E failed: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
