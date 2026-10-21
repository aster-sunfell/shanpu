# ===== build =====
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /workspace
COPY pom.xml .
RUN mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -DskipTests package

# ===== runtime =====
# Match .sdkmanrc (17.0.18+8); the digest also fixes the Alpine/JRE image contents.
FROM eclipse-temurin:17.0.18_8-jre-alpine@sha256:88c0002860cda56384d5ed3b2da4d0d9a2b44dc2ee4dc02344be985bd8b524bc AS runtime-base
WORKDIR /app
COPY docker/java.options /app/java.options
COPY docker/entrypoint.sh /app/entrypoint.sh
EXPOSE 8080
ENV JAVA_OPTS=""
ENV SPRING_PROFILES_ACTIVE=prod
ENTRYPOINT ["sh", "/app/entrypoint.sh"]

# CI supplies the instrumented jar, while retaining the production runtime.
FROM runtime-base AS experiment
COPY app.jar /app/app.jar

FROM runtime-base AS production
COPY --from=build /workspace/target/shanpu-backend-1.0.0-exec.jar /app/app.jar
