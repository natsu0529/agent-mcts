# semverrange

Implement `satisfies(version: str, range: str) -> bool` in `semverrange.py`.

It returns whether the version `version` is matched by the range `range`, following the
rules of npm's version ranges as described below. Use only the standard library.

## Versions

A version is `MAJOR.MINOR.PATCH`, optionally followed by `-PRERELEASE` and then by
`+BUILD`, as in [Semantic Versioning 2.0.0](https://semver.org/):

- `MAJOR`, `MINOR` and `PATCH` are non-negative integers without leading zeros
  (`0` is fine, `01` is not).
- `PRERELEASE` is one or more dot-separated identifiers made of `[0-9A-Za-z-]`. An
  identifier made only of digits is numeric and must not have leading zeros.
- `BUILD` is one or more dot-separated identifiers made of `[0-9A-Za-z-]`. It is
  ignored when comparing versions.
- A leading `v` is allowed (`v1.2.3`), and surrounding whitespace is ignored.

Versions are ordered as in SemVer 2.0.0 section 11:

- Compare `MAJOR`, `MINOR`, `PATCH` numerically, in that order.
- A version with a prerelease is lower than the same version without one
  (`1.0.0-alpha < 1.0.0`).
- Two prereleases are compared identifier by identifier from the left: numeric
  identifiers numerically, others in ASCII order, and a numeric identifier is always
  lower than a non-numeric one. If all compared identifiers are equal, the one with fewer
  identifiers is lower (`1.0.0-alpha < 1.0.0-alpha.1`).

## Ranges

A range is one or more **comparator sets** joined by `||`. A version matches the range
when it matches at least one set.

A comparator set is one or more **comparators** separated by whitespace. A version
matches the set when it matches every comparator in it (subject to the prerelease rule
below).

A primitive comparator is an operator followed by a full version: `<`, `<=`, `>`, `>=`
or `=`. No operator means `=`. An operator may be followed by whitespace
(`>= 1.2.3`).

The other forms below are shorthand that expand into primitive comparators.

### X-ranges and partial versions

`x`, `X` and `*` stand for "any" in the MAJOR, MINOR or PATCH position. A missing part
counts as `x`: `1.2` is `1.2.x` and `1` is `1.x.x`. An empty range or a set that is
just `*` matches any version.

| Range | Means |
|---|---|
| `*`, `x`, `""` | `>=0.0.0` |
| `1.x`, `1` | `>=1.0.0 <2.0.0-0` |
| `1.2.x`, `1.2` | `>=1.2.0 <1.3.0-0` |
| `>1`, `>1.x` | `>=2.0.0` |
| `>1.2` | `>=1.3.0` |
| `>=1.2` | `>=1.2.0` |
| `<1.2` | `<1.2.0-0` |
| `<=1.2` | `<1.3.0-0` |
| `<=1` | `<2.0.0-0` |
| `>*`, `<*` | matches nothing |

### Tilde ranges `~`

Allow patch-level changes if a minor version is given, minor-level changes if not.

| Range | Means |
|---|---|
| `~1.2.3` | `>=1.2.3 <1.3.0-0` |
| `~1.2` | `>=1.2.0 <1.3.0-0` |
| `~1` | `>=1.0.0 <2.0.0-0` |
| `~0.2.3` | `>=0.2.3 <0.3.0-0` |
| `~1.2.3-beta.2` | `>=1.2.3-beta.2 <1.3.0-0` |

### Caret ranges `^`

Allow changes that do not modify the left-most non-zero part of `MAJOR.MINOR.PATCH`.

| Range | Means |
|---|---|
| `^1.2.3` | `>=1.2.3 <2.0.0-0` |
| `^0.2.3` | `>=0.2.3 <0.3.0-0` |
| `^0.0.3` | `>=0.0.3 <0.0.4-0` |
| `^1.2.3-beta.2` | `>=1.2.3-beta.2 <2.0.0-0` |
| `^0.0.3-beta` | `>=0.0.3-beta <0.0.4-0` |
| `^1.2.x`, `^1.2` | `>=1.2.0 <2.0.0-0` |
| `^0.0.x`, `^0.0` | `>=0.0.0 <0.1.0-0` |
| `^1.x`, `^1` | `>=1.0.0 <2.0.0-0` |
| `^0.x`, `^0` | `>=0.0.0 <1.0.0-0` |

### Hyphen ranges `A - B`

An inclusive range. The spaces around `-` are required.

| Range | Means |
|---|---|
| `1.2.3 - 2.3.4` | `>=1.2.3 <=2.3.4` |
| `1.2 - 2.3.4` | `>=1.2.0 <=2.3.4` |
| `1.2.3 - 2.3` | `>=1.2.3 <2.4.0-0` |
| `1.2.3 - 2` | `>=1.2.3 <3.0.0-0` |

## Prereleases

A version that has a prerelease tag matches a comparator set only if, in addition to
matching every comparator, at least one comparator in the set is a full version with a
prerelease tag and the same `MAJOR.MINOR.PATCH` as the version. This includes the
`-0` bounds produced by the expansions above.

So `1.2.3-alpha.7` matches `>1.2.3-alpha.3`, but `3.4.5-alpha.9` does not, even though
it is greater than `1.2.3-alpha.3`. And `1.0.0-beta` does not match `*` or `<2.0.0`.

## Errors

Raise `ValueError` when `version` is not a valid version as defined above (`1.2`,
`01.2.3`, `1.2.3-01`, `1.2.3-`, `1.2.3.4`, `a.b.c`), or when `range` contains a
comparator that is not one of the forms above (`>>1.2.3`, `1.2.3.4`, `abc`).

## Running the tests

    python -m pytest -q
