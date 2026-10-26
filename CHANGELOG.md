# Changelog

All notable changes to Weasley Assistant are documented here.

## Unreleased

- Validate retrieval-gate decision, query and reason types before memory lookup.
  Malformed fields and blank positive queries fail open using the original
  message; omitted-field defaults and valid skip decisions remain unchanged.

## [0.2.0] - 2026-08-02

### Fixed
- Made `--help`, `help`, `--version`, and `version` lightweight CLI commands that exit successfully.
- Made gateway, operations, and skill help render without starting services or making network requests.
- Required `skill install` to receive exactly one URL and report invalid usage with exit code 2.
- Made unknown commands return exit code 2 with an actionable usage message.

### Changed
- Renamed the bundled Pi package example to `weasley-pokedex` and made it private by default.

### Removed
- Removed pre-Weasley import, environment-variable, and home-path compatibility surfaces.

## [0.1.1] - 2026-08-02

### Added
- Relaunched the project as the Apache-2.0-licensed Weasley Assistant.
- Added original Weasley artwork, local-first MCP support, and public-surface checks.
- Added a tokenless PyPI Trusted Publishing workflow with isolated build and publish jobs.

### Changed
- Moved the implementation and command surface to `weasley_assistant` and `weasley-assistant`.
- Rewrote installation, configuration, security, contribution, and architecture documentation for the public project.

### Removed
- Removed legacy whiteboards, screenshots, social assets, and filming-only documentation.
