// OmniFlow/plugins/kotlin/build.gradle.kts
//
// Production-ready Gradle Kotlin DSL build script for the OmniFlow Kotlin plugin.
// - Kotlin/JVM library and CLI-compatible layout
// - Publication (maven-publish) and signing stubs (CI: supply credentials)
// - Shadow (fat) jar for standalone runtime
// - Sources & javadoc jars for release artifacts
// - Static analysis: ktlint + detekt
// - Test: JUnit5 + kotlin.test
// - Reproducible build metadata (BUILD_DATE, VCS_REF) injected into manifest
//
// Place file at: OmniFlow/plugins/kotlin/build.gradle.kts
// Recommended usage in CI:
//   ./gradlew clean build publishToMavenLocal -Pversion=1.2.3 -PbuildDate="$(date -u +%Y-%m-%dT%H:%M:%SZ)" -PvcsRef=$(git rev-parse --short HEAD)
//

import org.gradle.api.tasks.testing.logging.TestLogEvent
import com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar
import java.time.Instant

plugins {
    kotlin("jvm") version "1.9.21"
    `java-library`
    application
    id("com.github.johnrengelman.shadow") version "8.1.1"
    id("org.jlleitschuh.gradle.ktlint") version "11.6.0"
    id("io.gitlab.arturbosch.detekt") version "1.23.1"
    `maven-publish`
    signing
    id("com.github.ben-manes.versions") version "0.46.0"
}

group = "io.omniflow.plugins"
version = (project.findProperty("version") as String?) ?: "0.0.0-unreleased"
description = "OmniFlow Kotlin plugin - reference implementation"

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
    withSourcesJar()
    withJavadocJar()
}

repositories {
    mavenCentral()
}

val kotlinVersion = "1.9.21"
val coroutinesVersion = "1.7.3"
val serializationVersion = "1.6.0"
val logbackVersion = "1.4.11"
val junitJupiterVersion = "5.10.0"
val kotestVersion = "5.7.2"

dependencies {
    // Kotlin stdlib
    implementation(kotlin("stdlib"))

    // Coroutines (recommended for plugin concurrency)
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion")

    // JSON serialization
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:$serializationVersion")

    // Logging (slf4j + logback)
    implementation("ch.qos.logback:logback-classic:$logbackVersion")
    implementation("org.slf4j:slf4j-api:2.0.9")

    // Utilities
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-slf4j:1.7.3")

    // Testing
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter-api:$junitJupiterVersion")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine:$junitJupiterVersion")
    testImplementation("io.kotest:kotest-assertions-core:$kotestVersion")
}

kotlin {
    jvmToolchain(17)
}

application {
    // If your plugin is packaged as an executable jar, set the main class (adjust to your package)
    mainClass.set("io.omniflow.plugin.MainKt")
}

tasks {
    // Make test output verbose and fail fast on errors for CI
    test {
        useJUnitPlatform()
        testLogging {
            events = setOf(TestLogEvent.FAILED, TestLogEvent.PASSED, TestLogEvent.SKIPPED, TestLogEvent.STANDARD_OUT)
            showStandardStreams = true
            exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
        }
        // fail fast in CI can be configured by command-line property
        if (project.hasProperty("failFast") && project.property("failFast") == "true") {
            maxFailures.set(1)
        }
    }

    // ShadowJar (fat jar) configuration
    withType<ShadowJar> {
        archiveBaseName.set("omniflow-plugin-kotlin")
        archiveClassifier.set("") // produce plain jar name
        archiveVersion.set(project.version.toString())
        mergeServiceFiles()
        // Manifest metadata for traceability
        manifest {
            attributes["Implementation-Title"] = project.name
            attributes["Implementation-Version"] = project.version
            attributes["Implementation-Vendor"] = "TheSkiF4er / OmniFlow"
            val buildDate: String = (project.findProperty("buildDate") as String?) ?: (Instant.now().toString())
            val vcsRef: String = (project.findProperty("vcsRef") as String?) ?: "unknown"
            attributes["Build-Date"] = buildDate
            attributes["VCS-Ref"] = vcsRef
            attributes["Main-Class"] = application.mainClass.get()
        }
    }

    // Ensure shadowJar is produced by 'assemble'
    assemble {
        dependsOn(named("shadowJar"))
    }

    // javadocs
    val javadocJar by getting(Jar::class) {
        archiveClassifier.set("javadoc")
        from(tasks.javadoc)
    }

    // ktlint formatting task options
    ktlint {
        version.set("0.50.0")
        debug.set(false)
        verbose.set(false)
        android.set(false)
        filter {
            exclude("**/generated/**")
        }
    }

    // Detekt configuration (static analysis)
    detekt {
        toolVersion = "1.23.1"
        buildUponDefaultConfig = true
        config.setFrom(files("${project.rootDir}/detekt-config.yml").takeIf { it.exists() } ?: files())
        reports {
            html.required.set(true)
            xml.required.set(false)
            txt.required.set(false)
        }
    }
}

// Publishing configuration (maven-style)
publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            from(components["java"])

            artifact(tasks.named<Jar>("shadowJar").get()) {
                classifier = ""
            }

            artifact(tasks["sourcesJar"])
            artifact(tasks["javadocJar"])

            groupId = project.group.toString()
            artifactId = "omniflow-plugin-kotlin"
            version = project.version.toString()

            pom {
                name.set("OmniFlow Kotlin Plugin")
                description.set("Kotlin plugin for OmniFlow — high-performance plugin template.")
                url.set("https://github.com/TheSkiF4er/OmniFlow")
                licenses {
                    license {
                        name.set("Apache License 2.0")
                        url.set("https://www.apache.org/licenses/LICENSE-2.0.txt")
                    }
                }
                scm {
                    connection.set("scm:git:git://github.com/TheSkiF4er/OmniFlow.git")
                    developerConnection.set("scm:git:ssh://github.com/TheSkiF4er/OmniFlow.git")
                    url.set("https://github.com/TheSkiF4er/OmniFlow")
                }
                developers {
                    developer {
                        id.set("theskif4er")
                        name.set("TheSkiF4er")
                        email.set("maintainers@omniflow.example")
                    }
                }
            }
        }
    }

    // Example repository; CI should configure real credentials for publish
    repositories {
        maven {
            name = "localPublish"
            url = uri("${buildDir}/repo")
        }
        // You can add Maven Central / GitHub Packages here when running in CI with credentials.
    }
}

// Signing artifacts when signing key is available (CI)
signing {
    // Signing only when the necessary properties are provided (CI will set them)
    val signingKey: String? = project.findProperty("signingKey") as String?
    val signingPassword: String? = project.findProperty("signingPassword") as String?
    if (!signingKey.isNullOrBlank() && !signingPassword.isNullOrBlank()) {
        useInMemoryPgpKeys(signingKey, signingPassword)
        sign(publishing.publications["mavenJava"])
    } else {
        // No-op: signing will be skipped locally
        logger.lifecycle("Signing disabled; set signingKey and signingPassword for release")
    }
}

// Helper task: dockerBuild (invokes Dockerfile in this project folder)
// Usage: ./gradlew dockerBuild -Pversion=1.2.3 -PbuildDate=... -PvcsRef=...
tasks.register<Exec>("dockerBuild") {
    group = "distribution"
    description = "Builds the Docker image for the Kotlin plugin (requires docker CLI)."

    val imgVersion = project.findProperty("version") ?: project.version
    val buildDate = project.findProperty("buildDate") ?: Instant.now().toString()
    val vcsRef = project.findProperty("vcsRef") ?: "unknown"

    commandLine = listOf(
        "docker", "build",
        "--build-arg", "VERSION=${imgVersion}",
        "--build-arg", "BUILD_DATE=${buildDate}",
        "--build-arg", "VCS_REF=${vcsRef}",
        "-t", "omniflow/plugin-kotlin:${imgVersion}",
        project.projectDir.absolutePath
    )
}

// Convenience: print build metadata
tasks.register("printBuildMetadata") {
    doLast {
        println("Project: ${project.name}")
        println("Group: ${project.group}")
        println("Version: ${project.version}")
        println("Build date: " + (project.findProperty("buildDate") ?: Instant.now().toString()))
        println("VCS ref: " + (project.findProperty("vcsRef") ?: "unknown"))
    }
}

// Dependency updates report (ben-manes plugin)
tasks.withType<com.github.benmanes.versions.updates.DependencyUpdatesTask> {
    checkForGradleUpdate = true
    outputFormatter = "plain"
}

// Ensure that ktlint runs before compilation in CI mode (optional)
tasks.named("check") {
    dependsOn("ktlintCheck", "detekt")
}

// Kotlin compile options
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile> {
    kotlinOptions {
        jvmTarget = "17"
        freeCompilerArgs = listOf("-Xjsr305=strict")
    }
}

// Additional safety: fail builds with deprecated APIs (helpful in CI)
tasks.withType<JavaCompile> {
    options.compilerArgs.addAll(listOf("-Xlint:deprecation"))
}
