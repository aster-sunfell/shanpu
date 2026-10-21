#!/bin/sh
set -eu
# Keep an explicit bound even when the container has no memory limit. A capped
# container uses the smaller cgroup value, including on kernels the JVM misdetects.
runtime_memory_limit=2147483648
for runtime_memory_file in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
    if [ -r "$runtime_memory_file" ]; then
        runtime_memory_value=$(cat "$runtime_memory_file")
        case "$runtime_memory_value" in
            ''|*[!0-9]*) ;;
            *) if [ "$runtime_memory_value" -gt 0 ] && [ "$runtime_memory_value" -lt "$runtime_memory_limit" ]; then
                   runtime_memory_limit=$runtime_memory_value
               fi ;;
        esac
    fi
done
# JAVA_OPTS retains the business image's explicit operator override mechanism.
exec java -XX:ActiveProcessorCount=2 "-XX:MaxRAM=$runtime_memory_limit" @/app/java.options ${JAVA_OPTS:-} -jar /app/app.jar
