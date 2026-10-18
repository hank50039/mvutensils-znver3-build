# MVUtensils Zen 3 build experiment

This repository contains a reproducible GitHub Actions workflow to build a test AVX2 variant of MVUtensils with the additional compiler tuning flag `-mtune=znver3`, while preserving the AVX2 ISA floor `-march=x86-64-v3`.

## Safety and scope

- Builds upstream MVUtensils source in a temporary Windows/MSYS2 CLANG64 runner.
- Does not modify or replace the user's installed plugin.
- Produces a downloadable build artifact for testing.
- The tuning change is experimental; it is not known to improve this workload until benchmarked.
- The upstream source is cloned at build time. The workflow prints the upstream commit SHA for reproducibility.
- This is not an official MVUtensils release.

## Run

The workflow starts when the workflow file is added/updated on the default branch. It can also be started manually from **Actions → Build Zen 3 AVX2 tuning** once GitHub enables Actions for the repository.

Download the artifact from the successful workflow run. Keep a backup of the existing DLL and test the new build in an isolated copy of the plugin directory.

## License

MVUtensils is licensed under GPL-2.0-or-later; see the upstream project for its license. This repository contains build automation and documentation only.
