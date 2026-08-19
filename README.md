# python-discover-dependents

Finds every repository in a GitHub organization that depends on a Python package, and resolves
the version to roll out to them.

This is the first half of a publisher-side dependency fan-out: when you release a package, this
works out who needs the upgrade, and
[`python-bump-dependents`](https://github.com/CVector-Energy/python-bump-dependents) raises the
pull request in each of them.

Consumers are **discovered, not listed**. Avoiding a hard-coded list of dependents in the
publisher's workflow is the whole point.

## Usage

Two jobs, because the fan-out is a matrix and only the caller can declare one.

```yaml
name: Bump dependents

on:
  release:
    types: [released]
  workflow_dispatch:

permissions:
  contents: read

# Never cancel: two runs racing on the same branch would have the second push rejected as
# non-fast-forward, and a run cancelled mid-fan-out leaves the consumers it had not reached on
# the previous version. Queueing means each release appends its commit in order.
concurrency:
  group: bump-dependents
  cancel-in-progress: false

jobs:
  discover:
    runs-on: ubuntu-24.04
    outputs:
      version: ${{ steps.discover.outputs.version }}
      repositories: ${{ steps.discover.outputs.repositories }}
      has-dependents: ${{ steps.discover.outputs.has-dependents }}
    steps:
      - uses: CVector-Energy/python-discover-dependents@v1
        id: discover
        with:
          package: my-package
          app-id: ${{ vars.APP_ID }}
          app-private-key: ${{ secrets.APP_PRIVATE_KEY }}

  bump:
    needs: discover
    if: needs.discover.outputs.has-dependents == 'true'
    runs-on: ubuntu-24.04
    strategy:
      fail-fast: false      # one broken consumer must not stop the others getting their PR
      max-parallel: 4
      matrix:
        repository: ${{ fromJSON(needs.discover.outputs.repositories) }}
    steps:
      - uses: CVector-Energy/python-bump-dependents@v1
        with:
          package: my-package
          repository: ${{ matrix.repository }}
          version: ${{ needs.discover.outputs.version }}
          app-id: ${{ vars.APP_ID }}
          app-private-key: ${{ secrets.APP_PRIVATE_KEY }}
```

### Waiting for a private index

Publishing usually runs off the same release event, so discovery can start before the wheel is
on the index. `wait-command` is polled until it exits zero; it runs with `PACKAGE` and `VERSION`
in the environment. Waiting on the artifact rather than on the other workflow keeps a manual
re-run correct too.

Configure cloud credentials in the caller — a composite action cannot host another action's
steps mid-sequence.

```yaml
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::000000000000:role/MyPackage-Bump
          aws-region: us-east-1

      - uses: CVector-Energy/python-discover-dependents@v1
        id: discover
        with:
          package: my-package
          app-id: ${{ vars.APP_ID }}
          app-private-key: ${{ secrets.APP_PRIVATE_KEY }}
          wait-command: |
            aws codeartifact describe-package-version \
              --domain my-domain --domain-owner 000000000000 \
              --repository internal --format pypi \
              --package "$PACKAGE" --package-version "$VERSION" >/dev/null 2>&1
```

Give that role **read-only** index access, not publish. The relock in the bump half resolves the
dependents' full dependency trees, and uv builds any sdist whose metadata it cannot read
statically — which runs third-party code in the same job that holds the registry token.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `package` | yes | | Package name as it appears in dependents' `pyproject.toml`. |
| `app-id` | yes | | Client ID of a GitHub App installed org-wide. |
| `app-private-key` | yes | | Private key of that app. |
| `owner` | no | calling repo's owner | Org to search. |
| `self` | no | calling repo | Repository name to exclude from results. |
| `version` | no | the release tag, `v` stripped | Version to roll out. |
| `repositories` | no | | Space-separated names to use instead of searching. |
| `allow-empty` | no | `false` | Treat "no dependents" as a no-op instead of an error. |
| `wait-command` | no | | Shell snippet polled until the version is installable. |
| `wait-attempts` | no | `30` | Attempts before giving up. |
| `wait-interval` | no | `20` | Seconds between attempts. |

## Outputs

| Output | Description |
| --- | --- |
| `version` | The resolved version, without a leading `v`. |
| `repositories` | JSON array of bare repository names, ready for `fromJSON` in a matrix. |
| `count` | How many dependents were found. |
| `has-dependents` | `"true"` when at least one was found. Gate the bump job on this. |
| `token` | The org-read GitHub App token, for callers that need to keep searching. |

## Behaviour worth knowing

**Discovery is a text search.** GitHub code search indexes default branches only, which is
exactly the set worth bumping, but it matches text — a `pyproject.toml` that names the package
in a comment can land in the results. `python-bump-dependents` filters those out by checking
`uv.lock` for a resolved entry, which is the resolver's own answer to whether the repository
depends on the package.

**Zero dependents is an error by default.** A search that silently returns nothing — an app
that lost its org installation, an index that hasn't caught up — otherwise looks exactly like a
run that bumped everything. Set `allow-empty: true` for a package published before anything
consumes it.

**A truncated search is an error too.** 1000 is the Search API's hard ceiling on total results.
Hitting it means the dependent list may be short, and silently bumping a subset is worse than
stopping.

**The app token is deliberately unscoped.** Code search spans the org, and naming the dependent
repositories in the workflow is the hard-coded list this action exists to avoid. It requests
read-only contents access; the write-capable tokens are minted per consumer, one repository
each, by the bump half.

## Development

```bash
uv run --group dev pytest
shellcheck scripts/*.sh
```

`scripts/discover.sh` holds the search and filtering, and is tested directly through a stub
`gh` — the output shape, self-exclusion, deduplication, and the truncation guard.

## Related projects

- [`python-bump-dependents`](https://github.com/CVector-Energy/python-bump-dependents) — the
  other half: takes one repository from the `repositories` output, moves the version specifier,
  relocks, and raises or refreshes the pull request.
- [`publish-to-codeartifact`](https://github.com/CVector-Energy/publish-to-codeartifact) —
  builds and publishes the wheel to AWS CodeArtifact. Puts the release on the index that
  `wait-command` waits for.
- [`pyproject-license-check`](https://github.com/CVector-Energy/pyproject-license-check) —
  audits a project's resolved dependencies for license compliance.
- [`python-test`](https://github.com/CVector-Energy/python-test) — runs ruff, mypy and pytest
  for a Python repository.
- [`aws-ga-deployment-role`](https://github.com/CVector-Energy/aws-ga-deployment-role) —
  Terraform module for the GitHub OIDC role the `wait-command` example assumes.
- [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token) —
  mints the org-read token this action searches with.

Consumer-side alternatives, which poll for upgrades instead of being pushed them:
[Dependabot](https://docs.github.com/en/code-security/dependabot) and
[Renovate](https://github.com/renovatebot/renovate). Both are less machinery than this; reach
for them first unless you need publisher-side fan-out or hit their gaps around `uv.lock` behind
a private index.

## License

MIT
