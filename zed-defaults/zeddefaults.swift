// zeddefaults — make Zed the default handler for programming file types on macOS.
//
// usage:
//   zeddefaults selftest           check the pure tag/guard logic (no system writes)
//   zeddefaults snapshot [path]    record current handlers as JSON (default: snapshot-before.json)
//   zeddefaults apply              point every target type at Zed
//   zeddefaults revert [path]      restore handlers recorded by a snapshot
//   zeddefaults list               print target -> UTI -> current handler bundle id
//   zeddefaults resolve            print target -> the app macOS would actually open it with
//   zeddefaults icons              print a stable hash of the Finder icon for each target
//   zeddefaults flush              re-register Zed, restart Finder and icon services
//
// build: swiftc -O -o zeddefaults zeddefaults.swift
//
// Everything below was learned the hard way; do not "simplify" it back:
//
//   * Types are resolved through the *filename tag* (UTTypeCreatePreferredIdentifierForTag),
//     never through NSURL.contentTypeKey. The latter content-sniffs and reports public.data
//     for `.gitignore`/`Makefile`, which silently redirected every untyped file on the
//     system to Zed.
//   * Setting a handler on a dyn.* UTI makes LaunchServices normalise it into a
//     `public.filename-extension` tag entry, which is what Finder's "Change All" writes.
//     That is why one call fixes both `public.python-script` and `dyn....` style types.
//   * The `com.apple.LaunchServices/com.apple.launchservices.secure` domain cannot be
//     written through CFPreferences: the write reports success and even reads back, but
//     never reaches the on-disk plist. Only the LaunchServices API persists, and a NULL
//     bundle id is rejected with paramErr (-50), so a type that had no handler before
//     cannot be put back. That is the one revert gap; `revert` names the affected types.
//   * public.html is locked to the default web browser: LSSetDefaultRoleHandlerForContentType
//     returns -54 even when the target is another browser, so it cannot be redirected.

import AppKit
import CoreServices
import Foundation

let zedBundleID = "dev.zed.Zed"
let zedPath = "/Applications/Zed.app"

// Types that must never be redirected, not even deliberately: every other file type
// conforms to these, so taking one hijacks files that have nothing to do with code.
let denylistedUTIs: Set<String> = [
    "public.item", "public.content", "public.text",
    "public.folder", "public.directory", "public.executable",
]

// The one broad type we DO take, on purpose. Files with no extension and nothing to
// sniff (.zshrc, .gitconfig, .gitignore, Dockerfile, Gemfile) resolve to exactly
// public.data, and it is the only lever that reaches them; their per-name dyn.* UTIs
// exist but Finder does not use them for these files.
//
// Verified blast radius (macOS 15.7.9): setting public.data does NOT leak into
// well-typed files -- .zip, .pdf, .png, .mp4, .dmg, .iso, .docx, .xlsx, .epub and .jar
// all kept their own handlers, because LaunchServices prefers the most specific binding.
// It only catches files that have no better type, e.g. `id_rsa`.
let untypedFallbackUTI = "public.data"

// Names with a dot in them whose association is bound to the whole name, not the extension.
let wholeNameTargets: Set<String> = ["CMakeLists.txt"]

// Layers A (source) and B (shared/edge types), exactly as agreed.
let targets: [String] = [
    // languages
    "a.py", "a.pyi", "a.js", "a.mjs", "a.cjs", "a.jsx", "a.ts", "a.tsx",
    "a.c", "a.h", "a.cc", "a.cpp", "a.cxx", "a.hpp", "a.hh", "a.hxx",
    "a.cs", "a.java", "a.kt", "a.kts", "a.scala", "a.go", "a.rs", "a.rb",
    "a.php", "a.swift", "a.m", "a.mm", "a.lua", "a.r", "a.dart", "a.vb",
    "a.clj", "a.cljs", "a.ml", "a.mli", "a.hs", "a.erl", "a.hrl",
    "a.ex", "a.exs", "a.fsx", "a.rkt", "a.scm",
    // scripts
    "a.sh", "a.bash", "a.zsh", "a.fish", "a.pl", "a.pm", "a.ps1", "a.bat", "a.cmd",
    // markup / web
    "a.html", "a.htm", "a.xhtml", "a.css", "a.scss", "a.sass", "a.less", "a.styl",
    "a.vue", "a.svelte", "a.astro", "a.xml", "a.svg", "a.md", "a.markdown",
    "a.rst", "a.tex", "a.txt", "a.csv",
    // data / config
    "a.json", "a.yaml", "a.yml", "a.toml", "a.ini", "a.cfg", "a.conf", "a.env",
    "a.properties", "a.lock", "a.sql", "a.ipynb", "a.gradle", "a.cmake", "a.mk",
    // build files
    "Makefile", "makefile", "Dockerfile", "dockerfile", "a.dockerfile",
    "Containerfile", "containerfile", "CMakeLists.txt", "Gemfile",
    // dotfile config (no extension -- bound by file name)
    "a.gitignore", ".gitignore", ".gitconfig", ".gitattributes", ".editorconfig",
    ".bashrc", ".bash_profile", ".bash_login", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv", ".zlogin", ".zlogout", ".profile",
]

// Representative files for the untyped fallback, which has no name of its own.
let untypedExamples = ["id_rsa", ".gitconfig", "Dockerfile"]

// MARK: - samples

let sampleDir = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("zeddefaults-samples", isDirectory: true)

func sampleURL(_ name: String) -> URL { sampleDir.appendingPathComponent(name) }

@discardableResult
func ensureSample(_ name: String) -> URL {
    let url = sampleURL(name)
    if !FileManager.default.fileExists(atPath: url.path) {
        try? "sample\n".write(to: url, atomically: true, encoding: .utf8)
    }
    return url
}

// MARK: - LaunchServices

/// The filename tag Finder matches on: the extension, or the whole name for
/// extension-less files such as `.gitignore` and `Makefile`.
func tags(for name: String) -> [String] {
    var out: [String] = []
    let ext = (name as NSString).pathExtension
    if ext.isEmpty {
        out.append(name.hasPrefix(".") ? String(name.dropFirst()) : name)
    } else {
        out.append(ext)
    }
    if wholeNameTargets.contains(name) { out.append(name) }
    return out
}

func utiForTag(_ tag: String) -> String? {
    guard let raw = UTTypeCreatePreferredIdentifierForTag(
        kUTTagClassFilenameExtension, tag as CFString, nil) else { return nil }
    return raw.takeRetainedValue() as String
}

func currentHandler(_ uti: String) -> String? {
    guard let raw = LSCopyDefaultRoleHandlerForContentType(
        uti as CFString, LSRolesMask.all) else { return nil }
    return raw.takeRetainedValue() as String
}

@discardableResult
func setHandler(_ uti: String, _ bundleID: String) -> OSStatus {
    LSSetDefaultRoleHandlerForContentType(uti as CFString, LSRolesMask.all, bundleID as CFString)
}

/// Ground truth: the app LaunchServices would hand this file to on double-click.
func openingApp(_ url: URL) -> String {
    guard let app = NSWorkspace.shared.urlForApplication(toOpen: url) else { return "-" }
    return app.path
}

// Read-only view of the tag entries the LaunchServices API created, shown by `list`
// for transparency. This domain cannot be written from here (see header).
let lsDomain = "com.apple.LaunchServices/com.apple.launchservices.secure" as CFString
let lsHandlersKey = "LSHandlers" as CFString

func tagEntryBundle(_ tag: String) -> String? {
    guard !tag.isEmpty,
          let handlers = CFPreferencesCopyAppValue(lsHandlersKey, lsDomain) as? [[String: Any]]
    else { return nil }
    for entry in handlers {
        guard let t = entry["LSHandlerContentTag"] as? String,
              entry["LSHandlerContentTagClass"] as? String == "public.filename-extension",
              t.compare(tag, options: .caseInsensitive) == .orderedSame else { continue }
        return entry["LSHandlerRoleAll"] as? String
    }
    return nil
}

// MARK: - targets

func resolvedTargets() -> [(name: String, tag: String, uti: String)] {
    var seen = Set<String>()
    var out: [(String, String, String)] = []

    func add(_ name: String, _ tag: String) {
        guard !tag.isEmpty, let uti = utiForTag(tag) else { return }
        if denylistedUTIs.contains(uti) {
            print("  skipped \(name): resolves to over-broad type \(uti)")
            return
        }
        guard seen.insert(uti).inserted else { return }  // many names share one UTI
        out.append((name, tag, uti))
    }

    for name in targets { for tag in tags(for: name) { add(name, tag) } }

    if seen.insert(untypedFallbackUTI).inserted {
        out.append(("(untyped files)", "", untypedFallbackUTI))
    }
    return out
}

/// Targets backed by a real file name, i.e. everything `resolve`/`icons` can sample.
func sampleTargets() -> [(name: String, tag: String, uti: String)] {
    resolvedTargets().filter { !$0.tag.isEmpty }
}

// MARK: - helpers

/// FNV-1a over the TIFF representation, stable across runs on the same machine.
func iconHash(_ url: URL) -> String {
    let image = NSWorkspace.shared.icon(forFile: url.path)
    guard let data = image.tiffRepresentation else { return "-" }
    var hash: UInt64 = 0xcbf2_9ce4_8422_2325
    for byte in data { hash = (hash ^ UInt64(byte)) &* 0x0000_0100_0000_01b3 }
    return String(hash, radix: 16)
}

@discardableResult
func run(_ launchPath: String, _ args: [String]) -> Int32 {
    guard FileManager.default.isExecutableFile(atPath: launchPath) else {
        print("  missing: \(launchPath)")
        return -1
    }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: launchPath)
    process.arguments = args
    do { try process.run() } catch {
        print("  failed: \(launchPath) \(error.localizedDescription)")
        return -1
    }
    process.waitUntilExit()
    return process.terminationStatus
}

// MARK: - commands

func cmdSelftest() {
    var failed = 0
    func check(_ condition: Bool, _ label: String) {
        if condition { print("  ok   \(label)") } else { failed += 1; print("  FAIL \(label)") }
    }
    func checkEqual(_ got: [String], _ want: [String], _ label: String) {
        check(got == want, "\(label) (got \(got), want \(want))")
    }

    checkEqual(tags(for: "a.py"), ["py"], "plain extension")
    checkEqual(tags(for: ".gitignore"), ["gitignore"], "dotfile drops the leading dot")
    checkEqual(tags(for: ".zshrc"), ["zshrc"], "shell rc dotfile")
    checkEqual(tags(for: "Makefile"), ["Makefile"], "extension-less bare name")
    checkEqual(tags(for: "a.dockerfile"), ["dockerfile"], "dotted name keeps its extension")
    checkEqual(tags(for: "CMakeLists.txt"), ["txt", "CMakeLists.txt"],
               "whole-name type gets both tags")

    check(utiForTag("py") == "public.python-script", "py resolves to public.python-script")
    check(utiForTag("gitignore")?.hasPrefix("dyn.") == true, "gitignore resolves to a dyn UTI")

    let resolved = resolvedTargets()
    check(!denylistedUTIs.contains(untypedFallbackUTI), "untyped fallback is not denylisted")
    check(resolved.contains { $0.uti == untypedFallbackUTI }, "untyped fallback is applied on purpose")
    check(!resolved.contains { denylistedUTIs.contains($0.uti) },
          "no denylisted type reaches the target list")
    check(resolved.count == Set(resolved.map { $0.uti }).count, "targets are unique per UTI")
    check(sampleTargets().count == resolved.count - 1, "only the untyped fallback has no sample file")

    print(failed == 0 ? "selftest: OK (\(resolved.count) targets)"
                      : "selftest: \(failed) FAILED")
    if failed > 0 { exit(1) }
}

func cmdSnapshot(_ path: String) {
    let entries = resolvedTargets().map { t -> [String: String] in
        ["target": t.name, "tag": t.tag, "uti": t.uti, "handler": currentHandler(t.uti) ?? ""]
    }
    let payload: [String: Any] = ["zed": zedBundleID, "entries": entries]
    guard let data = try? JSONSerialization.data(withJSONObject: payload,
                                                 options: [.prettyPrinted, .sortedKeys]) else {
        FileHandle.standardError.write(Data("snapshot: could not encode JSON\n".utf8))
        exit(1)
    }
    do { try data.write(to: URL(fileURLWithPath: path)) } catch {
        FileHandle.standardError.write(Data("snapshot: \(error.localizedDescription)\n".utf8))
        exit(1)
    }
    print("wrote \(entries.count) entries to \(path)")
}

func cmdApply() {
    var changed = 0, already = 0
    var blocked: [(String, String, OSStatus, String)] = []

    for t in resolvedTargets() {
        let before = currentHandler(t.uti) ?? "-"
        if before.compare(zedBundleID, options: .caseInsensitive) == .orderedSame {
            already += 1
            continue
        }
        let status = setHandler(t.uti, zedBundleID)
        // Read back rather than trusting the status: this is what catches a handler
        // write that quietly did nothing.
        let after = currentHandler(t.uti) ?? "-"
        if status == noErr, after.compare(zedBundleID, options: .caseInsensitive) == .orderedSame {
            changed += 1
            print("  \(t.uti)  \(before) -> \(zedBundleID)")
        } else {
            blocked.append((t.name, t.uti, status, after))
        }
    }

    print("apply: \(changed) changed, \(already) already Zed, \(blocked.count) blocked")
    guard blocked.isEmpty else {
        for b in blocked {
            print("  BLOCKED \(b.0) [\(b.1)] status=\(b.2), still \(b.3)")
        }
        print("""

        macOS refuses these types programmatically. public.html is bound to the default
        web browser and returns -54 even when the target is another browser, so it can
        only be changed by hand:
          Finder -> select a matching file -> Get Info -> Open With -> Zed -> Change All
        """)
        exit(1)
    }
}

func cmdRevert(_ path: String) {
    guard let data = FileManager.default.contents(atPath: path),
          let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let entries = payload["entries"] as? [[String: String]] else {
        FileHandle.standardError.write(Data("revert: cannot read snapshot \(path)\n".utf8))
        exit(1)
    }
    var restored = 0, failed = 0
    var unrecoverable: [String] = []
    for entry in entries {
        guard let uti = entry["uti"] else { failed += 1; continue }
        let handler = entry["handler"] ?? ""
        // A type that had no handler cannot be put back: LSSetDefaultRoleHandlerForContentType
        // rejects a NULL bundle id with paramErr (-50), and the secure plist cannot be written
        // from here. These keep pointing at Zed after a revert; say so instead of pretending.
        guard !handler.isEmpty else { unrecoverable.append(entry["target"] ?? uti); continue }
        let status = setHandler(uti, handler)
        if status == noErr {
            restored += 1
        } else {
            failed += 1
            print("  FAIL \(uti) -> \(handler) status=\(status)")
        }
    }
    print("revert: \(restored) restored, \(failed) failed, \(unrecoverable.count) unrecoverable")
    if !unrecoverable.isEmpty {
        print("  no API exists to remove a handler, so these stay on Zed: "
            + unrecoverable.joined(separator: ", "))
    }
}

func cmdList() {
    for t in resolvedTargets() {
        let handler = currentHandler(t.uti) ?? "-"
        let tagEntry = tagEntryBundle(t.tag).map { " tag=\($0)" } ?? ""
        print("\(t.name.padding(toLength: 22, withPad: " ", startingAt: 0)) "
            + "\(t.uti.padding(toLength: 40, withPad: " ", startingAt: 0)) \(handler)\(tagEntry)")
    }
}

func cmdResolve() {
    for t in sampleTargets() {
        print("\(t.name.padding(toLength: 22, withPad: " ", startingAt: 0)) \(openingApp(sampleURL(t.name)))")
    }
    print("---")
    for name in untypedExamples {
        print("\(name.padding(toLength: 22, withPad: " ", startingAt: 0)) \(openingApp(ensureSample(name)))")
    }
}

func cmdIcons() {
    for name in targets + untypedExamples {
        print("\(name.padding(toLength: 22, withPad: " ", startingAt: 0)) \(iconHash(ensureSample(name)))")
    }
    // References that make the hashes interpretable: if a target matches neither Zed's
    // app icon nor the generic document icon, Finder is showing a stale entry (or a
    // type-specific icon Zed never declared -- Zed ships only Document.icns).
    let unknown = sampleDir.appendingPathComponent("a.zzqunknown")
    try? "sample\n".write(to: unknown, atomically: true, encoding: .utf8)
    print("---")
    print("\("_ref:Zed.app".padding(toLength: 22, withPad: " ", startingAt: 0)) "
        + "\(iconHash(URL(fileURLWithPath: zedPath)))")
    print("\("_ref:generic-doc".padding(toLength: 22, withPad: " ", startingAt: 0)) "
        + "\(iconHash(unknown))")
}

func cmdFlush() {
    let lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        + "LaunchServices.framework/Support/lsregister"

    // Zed ships one document icon (Document.icns) against VS Code's 33, so every switched
    // type has to be rebound to it; without this, Finder keeps showing the old document
    // icon even though the association already changed.
    print("re-registering Zed with LaunchServices")
    print("  lsregister -f exit=\(run(lsregister, ["-f", zedPath]))")

    // Kill the agent that owns the per-user icon cache before deleting it, so it
    // rebuilds from the freshly registered database.
    print("  killall iconservicesagent exit=\(run("/usr/bin/killall", ["iconservicesagent"]))")

    let userCacheRoot = URL(fileURLWithPath: NSTemporaryDirectory()).deletingLastPathComponent()
    for name in ["C/com.apple.iconservices", "C/com.apple.iconservices.store"] {
        let cache = userCacheRoot.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: cache.path) {
            do {
                try FileManager.default.removeItem(at: cache)
                print("  removed \(cache.path)")
            } catch {
                print("  could not remove \(cache.path): \(error.localizedDescription)")
            }
        }
    }

    print("  killall Finder exit=\(run("/usr/bin/killall", ["Finder"]))")
    print("flush: done")
}

// MARK: - dispatch

let args = Array(CommandLine.arguments.dropFirst())
try? FileManager.default.createDirectory(at: sampleDir, withIntermediateDirectories: true)

switch args.first {
case "selftest": cmdSelftest()
case "snapshot": cmdSnapshot(args.count > 1 ? args[1] : "snapshot-before.json")
case "apply":    cmdApply()
case "revert":   cmdRevert(args.count > 1 ? args[1] : "snapshot-before.json")
case "list":     cmdList()
case "resolve":  cmdResolve()
case "icons":    cmdIcons()
case "flush":    cmdFlush()
default:
    print("""
    usage: zeddefaults <command>

      selftest          check the pure tag/guard logic (no system writes)
      snapshot [path]   record current handlers as JSON (default snapshot-before.json)
      apply             point every target type at Zed
      revert [path]     restore handlers recorded by a snapshot
      list              target -> UTI -> current handler bundle id
      resolve           target -> the app macOS would actually open it with
      icons             stable hash of the Finder icon for each target
      flush             re-register Zed, restart Finder and icon services
    """)
    exit(2)
}
