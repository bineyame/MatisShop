# ADR-001: Do not modify Odoo core

**Status:** accepted · **Date:** 2026-03 · **Deciders:** platform team

## Context

Odoo is large and it is tempting to "just fix" a core file when a hook is
missing. Every such edit has to be re-applied on every upgrade, is invisible to
`git diff` against upstream, and turns a routine version bump into an
archaeology exercise.

This platform is meant to serve several retailers over several Odoo versions.

## Decision

Odoo core source is never edited. All custom code lives in addons under
`addons/`, loaded through `addons_path`.

Where Odoo lacks a hook, we prefer, in order: an existing hook on a nearby
model; a model inheritance (`_inherit`); a scheduled job; and only then a
discussion about whether the requirement is right.

## Consequences

**Good:** upgrading Odoo is reading a changelog, not a merge. Upstream security
patches apply directly. A reviewer can diff our addons against nothing and see
100% of what we wrote.

**Bad:** occasionally we take a less elegant route. The POS receipt is the live
example — see ADR-004's consequences and `docs/fiscal-integration.md#receipt-output`.

**Enforcement:** the Odoo source is fetched into the image at build time and
`.git` is removed. There is no working copy to casually edit, and the running
container mounts `addons/` read-only.
