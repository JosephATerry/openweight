---
policy_id: EPG-ACCESS-001
title: Privileged Infrastructure Access Policy
domain: infrastructure-access
---
# Privileged Infrastructure Access Policy

## Purpose and scope

This fictional policy governs administrative access to production servers, cloud control planes, network devices, secrets stores, and database consoles operated by the Organization. It applies to employees, approved contractors, service accounts, and incident responders. Ordinary access to business applications is outside scope.

## Access requirements

Privileged access must be tied to a named user, a current role, and an approved access ticket. The ticket must identify the target environment, requested privilege, business purpose, approving system owner, and expiration time. Standing administrator access is limited to platform operations roles whose duties require it. All other access must be time-bound to no more than eight hours unless the infrastructure owner documents a shorter operational window.

Users must connect through the managed access gateway with phishing-resistant multi-factor authentication. Shared administrator accounts are prohibited except for documented break-glass accounts. Credentials, private keys, session tokens, and console output must not be copied into chat, source repositories, or personal storage.

## Approval and monitoring

The requester and approver must be different people. Production database access also requires approval from the data owner. Privileged sessions are logged and may be recorded. Audit records must include the user, ticket, target, commands or administrative actions, start time, and end time. Access reviews occur quarterly and when a worker changes roles.

Emergency access may be activated to contain an active incident when waiting for normal approval would materially increase harm. The incident commander must link the access to an incident record, limit its scope, and obtain retrospective owner review within one business day. Emergency access does not authorize bulk customer-data export or changes outside the containment plan.

## Revocation

Access must be revoked immediately when employment or a contract ends, the approved task is complete, suspicious activity is detected, or the user no longer has a business need. Failed MFA, expired training, or an expired ticket blocks a new session.
