# Changelog

## [2.4.0](https://github.com/calvindotsg/mac-upkeep/compare/v2.3.0...v2.4.0) (2026-04-20)


### Features

* add git_sync task with daily frequency and handler dispatch ([#35](https://github.com/calvindotsg/mac-upkeep/issues/35)) ([5f4628f](https://github.com/calvindotsg/mac-upkeep/commit/5f4628f5eaae9ddc6941fff72c2eb4cd841390ee))

## [2.3.0](https://github.com/calvindotsg/mac-upkeep/compare/v2.2.1...v2.3.0) (2026-04-13)


### Features

* add next scheduled run visibility to tasks and run ([6317ce1](https://github.com/calvindotsg/mac-upkeep/commit/6317ce18c041a8f29a8e8534a061bfd3aec24343))
* add next scheduled run visibility to tasks and run ([34dbe93](https://github.com/calvindotsg/mac-upkeep/commit/34dbe93fab3781d7ce669c852b6c06eb0ab8d5ca))
* redesign status command as scheduling dashboard ([#34](https://github.com/calvindotsg/mac-upkeep/issues/34)) ([86ca27c](https://github.com/calvindotsg/mac-upkeep/commit/86ca27cb7f7b1fd8878996552246061ebdd6d598))

## [2.2.1](https://github.com/calvindotsg/mac-upkeep/compare/v2.2.0...v2.2.1) (2026-04-09)


### Miscellaneous

* improve PyPI metadata and CI coverage ([#30](https://github.com/calvindotsg/mac-upkeep/issues/30)) ([7428b61](https://github.com/calvindotsg/mac-upkeep/commit/7428b610d1523d0e4b22e397185ad68e01240940))

## [2.2.0](https://github.com/calvindotsg/mac-upkeep/compare/v2.1.2...v2.2.0) (2026-04-09)


### Features

* suppress notification when all tasks skipped on boot ([62809e6](https://github.com/calvindotsg/mac-upkeep/commit/62809e6d8f1b687ec867691cdda1e86f81cd8dbd))
* suppress notification when all tasks skipped on boot ([8ae588c](https://github.com/calvindotsg/mac-upkeep/commit/8ae588cf9db5b4d3d117ba7a6fcc3f84104551c2))


### Bug Fixes

* update record.sh paths after demo.gif relocation ([#25](https://github.com/calvindotsg/mac-upkeep/issues/25)) ([ed59912](https://github.com/calvindotsg/mac-upkeep/commit/ed599128457e4aed64a7a2aa932c4a893d68d07c))


### Documentation

* align README, llms.txt, and reusable-patterns with RunAtLoad ([4e26c42](https://github.com/calvindotsg/mac-upkeep/commit/4e26c42e1aa02a2004d9fa76c9282f2563206f11))
* update scheduling docs for RunAtLoad ([eae69eb](https://github.com/calvindotsg/mac-upkeep/commit/eae69eb96bfef82b6fb5ae6675590c965fa5f971))


### CI/CD

* derive release bot identity dynamically from app-slug ([#27](https://github.com/calvindotsg/mac-upkeep/issues/27)) ([afc085a](https://github.com/calvindotsg/mac-upkeep/commit/afc085ae660b45373ca9b68047abd7612bdd4ec2))

## [2.1.2](https://github.com/calvindotsg/mac-upkeep/compare/v2.1.1...v2.1.2) (2026-04-09)


### Documentation

* use absolute URL for demo GIF, move to demo/ ([#23](https://github.com/calvindotsg/mac-upkeep/issues/23)) ([4b995cc](https://github.com/calvindotsg/mac-upkeep/commit/4b995cca20df73d39604bd1bcadf666ca4eeb213))

## [2.1.1](https://github.com/calvindotsg/mac-upkeep/compare/v2.1.0...v2.1.1) (2026-04-08)


### Documentation

* rewrite README and CLAUDE.md for public audience ([#21](https://github.com/calvindotsg/mac-upkeep/issues/21)) ([710712d](https://github.com/calvindotsg/mac-upkeep/commit/710712d317b560d6039fca6aeaa33dae5f5f4e50))


### Miscellaneous

* update repository URLs after GitHub rename ([#19](https://github.com/calvindotsg/mac-upkeep/issues/19)) ([ec97db4](https://github.com/calvindotsg/mac-upkeep/commit/ec97db45be8b9797f400ff45a329959a388efeab))

## [2.1.0](https://github.com/calvindotsg/maintenance/compare/v2.0.0...v2.1.0) (2026-04-08)


### Features

* custom task support, detect auto-inference, validation ([#16](https://github.com/calvindotsg/maintenance/issues/16)) ([d8af4e6](https://github.com/calvindotsg/maintenance/commit/d8af4e62620ebbf1088e0e55c9faf7cdbe0f0785))
* PyPI publishing and platform guard ([#18](https://github.com/calvindotsg/maintenance/issues/18)) ([af5e341](https://github.com/calvindotsg/maintenance/commit/af5e341e0798e7cd5e997de5d9dea17897ff369e))

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
