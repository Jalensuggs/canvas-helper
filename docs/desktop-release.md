# Desktop install and release

Install platform prerequisites from the Tauri 2 documentation, then:

```bash
make install-desktop
make desktop-build
```

The build first compiles `frontend/dist`, freezes the FastAPI backend with its
migrations, then places the target-triple sidecar under `src-tauri/binaries`.
Tauri creates platform installers under `src-tauri/target/release/bundle`.

Before distributing a release:

1. Run `make check`, a clean desktop build on each target OS, and install the
   resulting artifact on a clean machine.
2. Verify first launch, Keychain access, sleep/resume sync, single-instance
   focus, and clean shutdown.
3. Configure platform signing/notarization credentials only in protected CI.
4. Publish checksums and signed artifacts together. This repository does not
   auto-publish releases.

The backend chooses an OS-assigned loopback port and publishes it atomically in
the app-data directory. Tauri owns and terminates that process.
