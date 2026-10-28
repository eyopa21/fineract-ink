# Reproducible build of Apache Fineract from the pinned tag with the Part A patch applied.
# Needs only Docker. Slow (~20 min cold, 6 GB RAM for Gradle). The default path is `make build-fineract`
# which uses Jib on the host; this Dockerfile exists so reviewers without a JDK can rebuild the image.
#
#   docker build -f fineract-customization/Dockerfile -t fineract-custom:1.15.0 fineract-customization

FROM azul/zulu-openjdk-alpine:21 AS build
ARG FINERACT_TAG=1.15.0
RUN apk add --no-cache git bash
WORKDIR /src
RUN git clone --depth 1 --branch ${FINERACT_TAG} https://github.com/apache/fineract.git .
# Gradle asks for a 12 GB daemon heap; keep the required --add-exports flags but cap the heap.
RUN mkdir -p /root/.gradle && grep '^org.gradle.jvmargs' gradle.properties | sed 's/-Xmx12g/-Xmx4g/' > /root/.gradle/gradle.properties
COPY gl-external-reference.patch /tmp/gl-external-reference.patch
RUN if [ -s /tmp/gl-external-reference.patch ]; then git apply --check /tmp/gl-external-reference.patch && git apply /tmp/gl-external-reference.patch && echo "patch applied"; else echo "empty patch: building unmodified ${FINERACT_TAG}"; fi
RUN ./gradlew :fineract-provider:bootJar -x test --no-daemon --console=plain \
 && cp fineract-provider/build/libs/fineract-provider-*.jar /fineract-provider.jar

FROM azul/zulu-openjdk-alpine:21
WORKDIR /app
COPY --from=build /fineract-provider.jar /app/fineract-provider.jar
RUN mkdir -p /app/plugins && chown -R nobody:nogroup /app
USER nobody:nogroup
EXPOSE 8080 8443
ENTRYPOINT ["java", "-Duser.home=/tmp", "-Dfile.encoding=UTF-8", "-Duser.timezone=UTC", "-Djava.security.egd=file:/dev/./urandom", "-Dloader.path=/app/plugins", "-jar", "/app/fineract-provider.jar"]
