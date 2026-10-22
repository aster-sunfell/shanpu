#!/bin/sh
set -eu
# Explicit heap bounds are shared with host development; no container/host percentage.
# JAVA_OPTS retains the business image's explicit operator override mechanism.
exec java -XX:ActiveProcessorCount=2 @/app/java.options ${JAVA_OPTS:-} -jar /app/app.jar
