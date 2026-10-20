FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY app.jar /app/app.jar
ENV SPRING_PROFILES_ACTIVE=prod,experiment,sut
ENTRYPOINT ["java", "-Xms128m", "-Xmx256m", "-Xss512k", "-XX:MaxDirectMemorySize=48m", "-XX:ActiveProcessorCount=2", "-XX:+ExitOnOutOfMemoryError", "-Duser.timezone=Asia/Shanghai", "-jar", "/app/app.jar"]
