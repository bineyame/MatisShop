# ADR-011: Pin Odoo 18 Community to an explicit revision, built from source

**Status:** accepted · **Date:** 2026-03

## Context

"Odoo 18" is not a version, it is a moving branch. A rebuild three weeks later
produces different code, and a bug that appears on one machine cannot be
reproduced on another. For a platform that will be deployed to several
customers and reviewed by people who were not present when it was built,
reproducibility is not optional.

Three options were considered:

1. **the official `odoo:18.0` Docker image alone** — convenient, but the tag
   floats and the code we run is not identified by a revision we chose;
2. **a git submodule of odoo/odoo** — genuinely pins a revision, but forces
   every clone to fetch a very large repository and puts an editable working
   copy of Odoo core in the tree, which invites exactly the edits ADR-001
   forbids;
3. **a custom image built from a pinned checkout** — pins the revision, keeps
   the source out of the working tree.

## Decision

Option 3. `odoo/Dockerfile` starts `FROM odoo:18.0` — which supplies the exact
OS packages, Python dependencies and wkhtmltopdf build Odoo 18 expects — and
then fetches exactly one commit of the official source:

```dockerfile
ARG ODOO_REVISION            # d60ab9c928f0ea3d31ef11fc54bf3b9549b082e8
RUN git init . \
 && git remote add origin https://github.com/odoo/odoo.git \
 && git fetch --depth 1 origin "${ODOO_REVISION}" \
 && git checkout -q FETCH_HEAD \
 && git rev-parse HEAD > /opt/odoo/REVISION \
 && rm -rf .git
```

The entrypoint runs `odoo-bin` from `/opt/odoo/source`. Because Python puts a
script's own directory first on `sys.path`, the pinned source shadows the
distro package completely — the code that runs is the code we pinned.

The revision is `ODOO_REVISION` in `compose.yaml` and overridable from `.env`.
The container logs the revision it is running at every start.

## Consequences

**Good:** a clean checkout always builds the same Odoo. The running revision is
printed in the logs and readable at `/opt/odoo/REVISION`. There is no editable
copy of Odoo core in the working tree, so ADR-001 is enforced by construction
rather than by discipline. A shallow single-commit fetch is far smaller than a
full clone.

**Bad:** the first build takes several minutes and needs network access to
GitHub. `.git` is removed, so you cannot `git log` inside the container — the
pinned SHA is recorded in `compose.yaml` and in `/opt/odoo/REVISION` instead.

**Residual risk:** the base image supplies dependency versions while the source
supplies application code, so in principle they could drift. In practice both
track 18.0 and this is the standard way the official image is built. Upgrading
the revision means also refreshing the base image, and that is a deliberate,
reviewable change.

**To upgrade:** `git ls-remote https://github.com/odoo/odoo.git refs/heads/18.0`,
update `ODOO_REVISION`, rebuild, run `make test` and `make verify`.
