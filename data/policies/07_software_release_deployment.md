---
policy_id: EPG-RELEASE-007
title: Software Release and Production Deployment Policy
domain: software-release
---
# Software Release and Production Deployment Policy

## Purpose and scope

This fictional policy covers changes to production applications, infrastructure as code, configuration, database schemas, feature flags, and deployment pipelines. Its goal is to preserve service reliability, security, traceability, and separation of duties without preventing well-controlled delivery.

## Standard change requirements

Every production change must be linked to a reviewed source change and a deployment record. The record must identify the service, owner, risk, test evidence, dependencies, rollout plan, monitoring signals, and rollback procedure. Automated tests and required security checks must pass. At least one qualified reviewer other than the author must approve the change.

Deployments must use the managed pipeline and a named identity protected by multi-factor authentication. Direct production shell access is not a normal deployment method. Pipeline service accounts receive only the privileges needed for their environments, and secrets must come from the approved secrets store. Changes involving customer data, payment processing, or access controls require the relevant system owner to confirm the release window.

## Rollout and verification

Teams should use staged rollout, health checks, and observable success criteria where supported. The deployer must monitor error rates, latency, security alerts, and business transactions during the verification period. A failed success criterion triggers rollback or an explicitly documented decision to continue.

## Emergency changes

An active incident may justify an emergency change with abbreviated advance review. The incident commander must record the reason, authorized deployer, affected systems, expected risk, and rollback plan. Privileged access still follows the Privileged Infrastructure Access Policy. Emergency status does not permit disabling audit logs or copying production data into an unapproved environment.

Emergency changes require peer review and complete test evidence as soon as conditions stabilize, no later than the next business day. Repeated emergency changes for the same weakness must result in corrective work rather than permanent bypass of the standard process.
