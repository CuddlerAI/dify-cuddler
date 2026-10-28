# Source and release provenance

The plugin runtime is MIT-licensed CuddlerAI code. This standalone release contains only the narrow Dify plugin runtime and release documentation, not the private product repository or its database/server implementation.

The 0.3.6 runtime is byte-identical to the previously packaged Dify Cloud candidate whose package SHA-256 was `7e67afbfdbabe24a8a69bb571db065450a66461adbb06d616aa7206e6072df94`. This public release changes README, privacy, and provenance documentation only; those documentation changes produce a different package checksum. It does not rerun or extend the historical acceptance evidence.

Version 0.3.7 adds the required `repo` and `contact` manifest fields after Marketplace pre-check feedback, bumps the package version, and updates release documentation. All Python runtime, tool/provider definitions, dependencies, privacy policy, and artwork remain byte-identical to public 0.3.6 (package SHA-256 `1779d8028100738a7a464674860904e3642152ea286201e95953360074b5193a`). Public 0.3.6 tags and packages remain unchanged. No additional Cloud acceptance is claimed.

Version 0.3.8 changes only the English and Simplified Chinese display labels in the manifest and provider, the package version, and release documentation. The public name is Cuddler; the existing `cuddler-ai/cuddler_recovery` identifier is unchanged. All Python runtime, tool definitions, dependencies, privacy policy, and artwork remain byte-identical to public 0.3.7 (package SHA-256 `b52053516930df0b06a1e8e54cb969dd38d70e2a7d9ea50e7e7d105e25a8b75c`). No additional Cloud acceptance is claimed.

Packaging uses the official Dify plugin daemon CLI v0.6.10. The runtime dependency is the public official `dify-plugin==0.10.2` SDK; dependencies retain their respective licenses. No private SDK, secrets, local credentials, test fixtures, workflow history, or raw acceptance ledger is distributed.

Real Cloud successes and unverified boundaries are listed in README.md. The maintainer explicitly elected to release without additional acceptance runs. Marketplace submission, review, and public listing are distinct states.

The MIT source license does not grant additional rights to Cuddler names, icons, or trademarks.
