FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    git \
    wget \
    unzip \
    curl \
    zip \
    && rm -rf /var/lib/apt/lists/*

# ── SDKMAN (manages Java, Maven, Gradle versions at runtime) ───
RUN curl -s "https://get.sdkman.io" | bash
ENV SDKMAN_DIR="/root/.sdkman"
ENV PATH="${SDKMAN_DIR}/candidates/java/current/bin:${SDKMAN_DIR}/candidates/maven/current/bin:${SDKMAN_DIR}/candidates/gradle/current/bin:${PATH}"

# ── Default versions baked in (covers most projects) ──────────
RUN bash -c "source /root/.sdkman/bin/sdkman-init.sh \
    && sdk install java 17.0.9-tem \
    && sdk install maven 3.9.6 \
    && sdk install gradle 8.5"

# ── Verify defaults ────────────────────────────────────────────
RUN bash -c "source /root/.sdkman/bin/sdkman-init.sh \
    && java -version \
    && mvn -version \
    && gradle -version"

# ── Spoon JAR ──────────────────────────────────────────────────
COPY spoon-analysis/my_spoon_wrapper-1.0-shaded.jar /app/my_spoon_wrapper-1.0-shaded.jar

# ── Python pipeline scripts ────────────────────────────────────
COPY src/ /app/src/
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── Entrypoint ─────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Workspace (all runtime files live here) ────────────────────
RUN mkdir -p /workspace
WORKDIR /workspace

ENTRYPOINT ["/entrypoint.sh"]