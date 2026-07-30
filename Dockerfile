# syntax=docker/dockerfile:1
#
# Crucible image. Satisfies CONTRACT.md §8:
#   - every FROM digest-pinned (items 4)
#   - conda environment from a committed, fully-pinned environment.lock.yml,
#     activated for the entrypoint, and NOT the installation's base (2, 3)
#   - multi-stage: no compiler, curl, wget or nc in the final image (5)
#   - numeric non-zero USER (6); code owned by uid 0 and not writable by it (11)
#   - runs read-only, cap-drop ALL, no-new-privileges, --network none (7)
#   - no package-manager caches retained (12)
#   - no accelerator runtime of any kind (16)
#
# Digests below were resolved from the registry at authoring time. To refresh:
#   docker buildx imagetools inspect condaforge/miniforge3:24.9.2-0
#   docker buildx imagetools inspect debian:12-slim

ARG BUILDER_IMAGE=condaforge/miniforge3:24.9.2-0@sha256:937dc1e8ab9ffc5f388f0d2a1ce5d24ba5b15850771571d7442fac1af53f3fad
ARG RUNTIME_IMAGE=debian:12-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

# ---- build stage: resolve the conda environment -----------------------------
FROM ${BUILDER_IMAGE} AS build

# Build from the lock file, not environment.yml: the lock is what pins the
# transitive set.
#
# Channel selection is done INSIDE the lock file, which lists conda-forge and
# `nodefaults`. That is what keeps the `defaults` channel -- and with it MKL,
# several hundred megabytes for no benefit here -- out of the solve.
# `conda env create` does not accept --override-channels/--channel; those are
# `conda create` flags, and passing them here fails the build outright.
COPY environment.lock.yml /tmp/environment.lock.yml
RUN conda env create --quiet --file /tmp/environment.lock.yml --prefix /opt/crucible-env \
    && conda clean --all --force-pkgs-dirs --yes \
    && find /opt/crucible-env -follow -type f -name '*.a' -delete \
    && find /opt/crucible-env -follow -type f -name '*.pyc' -delete \
    && find /opt/crucible-env -follow -type d -name '__pycache__' -prune -exec rm -rf {} +

# ---- final stage: minimal runtime, non-root ---------------------------------
FROM ${RUNTIME_IMAGE} AS final

# The environment is relocatable and self-contained, so only it comes across.
# Nothing from the builder's conda installation, package cache, or toolchain
# follows it. Owned by uid 0 and never writable by the runtime user.
COPY --from=build --chown=0:0 /opt/crucible-env /opt/crucible-env

# Application code, also owned by uid 0: code the running process can rewrite
# is code an input can rewrite.
WORKDIR /app
COPY --chown=0:0 src/ /app/src/

# sys.prefix is /opt/crucible-env, which is a named prefix environment and is
# emphatically not the conda root -- there is no conda installation in this
# image at all. PYTHONDONTWRITEBYTECODE keeps a read-only rootfs from being a
# problem on import.
ENV PATH="/opt/crucible-env/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CRUCIBLE_IN_CONTAINER=1

# CONTRACT.md §8 item 10: the graded container checks inspect the image through
# /bin/sh, id, find, du and awk. Assert they are present at build time rather
# than discovering at review time that a slimmer base dropped one. This RUN
# also proves the interpreter starts and the package imports.
RUN set -eu; \
    for t in sh id find du awk; do \
        command -v "$t" >/dev/null || { echo "FATAL: required tool '$t' missing" >&2; exit 1; }; \
    done; \
    python -c "import sys, jsonschema, crucible; assert sys.prefix == '/opt/crucible-env', sys.prefix" ; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /root/.cache 2>/dev/null || true

# Numeric, non-zero uid. No /etc/passwd entry is required by anything below,
# and no supplementary group membership is assumed.
USER 10001:10001

ENTRYPOINT ["python", "-m", "crucible"]
CMD ["--help"]
