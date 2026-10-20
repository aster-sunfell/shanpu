# ===== build =====
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /workspace
COPY pom.xml .
RUN mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -DskipTests package

# ===== runtime =====
FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /workspace/target/shanpu-backend-1.0.0-exec.jar app.jar
EXPOSE 8080
ENV JAVA_OPTS="-XX:+UseG1GC"
ENV JAVA_TOOL_OPTIONS="-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m"
ENV SPRING_PROFILES_ACTIVE=prod
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
