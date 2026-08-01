# Changelog

All notable changes to Weasley Assistant are documented here.

## [0.1.1] - 2026-08-02

### Added
- Relaunched the project as the Apache-2.0-licensed Weasley Assistant.
- Added original Weasley artwork, local-first MCP support, migration tests, and public-surface checks.
- Added a tokenless PyPI Trusted Publishing workflow with isolated build and publish jobs.

### Changed
- Moved the implementation and command surface to `weasley_assistant` and `weasley-assistant`.
- Kept only a narrow `waku` import/command compatibility shim for existing local users.
- Rewrote installation, configuration, security, contribution, and architecture documentation for the public project.

### Removed
- Removed legacy whiteboards, screenshots, social assets, and filming-only documentation.
