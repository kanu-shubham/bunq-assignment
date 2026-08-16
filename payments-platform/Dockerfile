# =============================================================================
# One Dockerfile for every service. docker-compose passes MODULE to select which.
#
# WHY A MULTI-STAGE BUILD
#   The build stage needs Maven, a JDK and the whole dependency cache — around 800MB.
#   The runtime needs a JRE and one jar. Separating them means the shipped image contains
#   no compiler, no build tooling and no source code: smaller to pull, and a smaller
#   attack surface, since a vulnerability in Maven cannot be exploited in something that
#   does not ship Maven.
# =============================================================================

# ---------- stage 1: build (shared by all services, so Docker caches it once) ----------
FROM maven:3.9-eclipse-temurin-21 AS build

WORKDIR /build

# Copy only the POMs first and pre-download dependencies. Docker caches each layer by its
# inputs, so this expensive step is re-run only when a pom.xml changes — not on every
# source edit. Getting this ordering wrong turns a 10-second rebuild into a 4-minute one.
COPY pom.xml .
COPY common-lib/pom.xml common-lib/
COPY discovery-server/pom.xml discovery-server/
COPY api-gateway/pom.xml api-gateway/
COPY auth-service/pom.xml auth-service/
COPY payment-service/pom.xml payment-service/
COPY ledger-service/pom.xml ledger-service/
COPY webhook-service/pom.xml webhook-service/
COPY acquirer-simulator/pom.xml acquirer-simulator/
RUN mvn -B -q dependency:go-offline -DskipTests || true

COPY . .
RUN mvn -B -q clean package -DskipTests

# ---------- stage 2: extract Spring Boot layers ----------
FROM eclipse-temurin:21-jre-alpine AS extract
ARG MODULE
WORKDIR /extract
COPY --from=build /build/${MODULE}/target/*.jar application.jar

# A Spring Boot fat jar can be split into layers ordered by how often they change:
# dependencies (rarely) ... application (every commit). Copying them as separate Docker
# layers means a code change re-uploads a few hundred KB instead of a 60MB jar.
RUN java -Djarmode=tools -jar application.jar extract --layers --launcher --destination extracted

# ---------- stage 3: runtime ----------
FROM eclipse-temurin:21-jre-alpine
ARG MODULE
WORKDIR /app

# Never run as root. A container escape from a root process owns the host; from an
# unprivileged one, far less. This is one line and it is not optional.
RUN addgroup -S paykit && adduser -S paykit -G paykit
USER paykit

# Ordered least- to most-frequently-changed, matching the layer ordering above.
COPY --from=extract --chown=paykit:paykit /extract/extracted/dependencies/ ./
COPY --from=extract --chown=paykit:paykit /extract/extracted/spring-boot-loader/ ./
COPY --from=extract --chown=paykit:paykit /extract/extracted/snapshot-dependencies/ ./
COPY --from=extract --chown=paykit:paykit /extract/extracted/application/ ./

ENV MODULE=${MODULE}
ENV JAVA_OPTS="-XX:MaxRAMPercentage=75 -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError"

# MaxRAMPercentage, not -Xmx: the JVM reads the *container's* memory limit rather than the
# host's. Without it, a JVM in a 512MB container happily sizes its heap for a 64GB machine
# and gets OOM-killed by the kernel with no stack trace to explain why.
#
# ExitOnOutOfMemoryError: a JVM that has run out of heap is not going to recover. Dying lets
# the orchestrator replace it; limping along serves errors indefinitely.

EXPOSE 8080

# exec form, so the JVM is PID 1 and receives SIGTERM directly. With the shell form, the
# shell is PID 1, swallows the signal, and Docker kills the container after the grace
# period — turning every graceful shutdown into a hard kill.
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS org.springframework.boot.loader.launch.JarLauncher"]
