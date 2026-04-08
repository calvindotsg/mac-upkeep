# Changelog

## [2.0.0](https://github.com/calvindotsg/maintenance/compare/v1.2.1...v2.0.0) (2026-04-08)


### ⚠ BREAKING CHANGES

* Package renamed from maintenance to mac-upkeep.

### Features

* rename package to mac-upkeep ([#15](https://github.com/calvindotsg/maintenance/issues/15)) ([aef9e86](https://github.com/calvindotsg/maintenance/commit/aef9e8604f3c3a95286f06274e7737d81ec1dc5a))
* TOML-driven task registry + init/show-config commands ([#12](https://github.com/calvindotsg/maintenance/issues/12)) ([23a7f0b](https://github.com/calvindotsg/maintenance/commit/23a7f0b53931eae733a8a7bf9cf141e5c0460797))


### Documentation

* update README and CLAUDE.md for TOML-driven architecture ([#14](https://github.com/calvindotsg/maintenance/issues/14)) ([2ce0583](https://github.com/calvindotsg/maintenance/commit/2ce058350b5d0665113ec3064d299ce777709a30))


### CI/CD

* keep uv.lock in sync with release-please version bumps ([#10](https://github.com/calvindotsg/maintenance/issues/10)) ([d6a81a9](https://github.com/calvindotsg/maintenance/commit/d6a81a9c72cb3a764708d74e505b0f5a86a14697))

## [1.2.1](https://github.com/calvindotsg/maintenance/compare/v1.2.0...v1.2.1) (2026-04-07)


### Bug Fixes

* --force filter, frequency scheduling, task discoverability ([#9](https://github.com/calvindotsg/maintenance/issues/9)) ([26a5ac1](https://github.com/calvindotsg/maintenance/commit/26a5ac123406836266480f246ba4c7f1b8789cd7))


### Documentation

* **claude:** document v1.2.0 patterns for AI agent effectiveness ([#7](https://github.com/calvindotsg/maintenance/issues/7)) ([a6d5f0d](https://github.com/calvindotsg/maintenance/commit/a6d5f0dca8c51cb2b97e71ea35c95771f9fb8abf))

## [1.2.0](https://github.com/calvindotsg/maintenance/compare/v1.1.1...v1.2.0) (2026-04-07)


### Features

* add brew tasks, frequency scheduling, live TUI, and actionable notifications ([#5](https://github.com/calvindotsg/maintenance/issues/5)) ([9ee2381](https://github.com/calvindotsg/maintenance/commit/9ee23811fa60fbb483b4ff695aabc92a8c23899c))

## [1.1.1](https://github.com/calvindotsg/maintenance/compare/v1.1.0...v1.1.1) (2026-04-06)


### Bug Fixes

* **tasks:** close stdin and add --force to uv cache prune ([#4](https://github.com/calvindotsg/maintenance/issues/4)) ([4692060](https://github.com/calvindotsg/maintenance/commit/46920604eaccf8e8402d8cb0e70bb3c0e4b22606))


### Documentation

* **claude:** add release-please manifest and token scoping constraints ([8cd3775](https://github.com/calvindotsg/maintenance/commit/8cd377570eb332fe00ef1367fc1743af84b2e667))

## [1.1.0](https://github.com/calvindotsg/maintenance/compare/v1.0.0...v1.1.0) (2026-04-05)


### Features

* rich output, macOS notifications, and tap automation ([#1](https://github.com/calvindotsg/maintenance/issues/1)) ([c0d9577](https://github.com/calvindotsg/maintenance/commit/c0d9577c13dcf4902af37b3c4a72d448fb9e93a3))


### Bug Fixes

* add chmod 0440 to sudoers setup instructions ([060d848](https://github.com/calvindotsg/maintenance/commit/060d848de637e9db72168336d04ac516d2fb3ec4))


### Miscellaneous

* add release-please manifest and gitignore .scratchpad ([0e8d983](https://github.com/calvindotsg/maintenance/commit/0e8d983826ec2735667ed321e8ee77a5c2175343))
