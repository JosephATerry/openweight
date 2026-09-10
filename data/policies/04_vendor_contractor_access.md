---
policy_id: EPG-VENDOR-004
title: Vendor and Contractor Access Policy
domain: vendor-contractor-access
---
# Vendor and Contractor Access Policy

## Purpose and sponsorship

This fictional policy applies when a supplier, consultant, temporary worker, or other non-employee needs access to Organization systems or data. Every external user must have an internal sponsor accountable for the business need, requested scope, and timely removal of access.

Before provisioning, Procurement must confirm an active agreement and the system owner must confirm required security and privacy terms. The request must identify each person, employer, target system, role, work location, start date, and end date. Generic vendor identities and credential sharing are prohibited. Access must not begin before screening, confidentiality obligations, and required security training are complete.

## Technical controls

Contractors must use managed or explicitly approved devices, a unique managed identity, and multi-factor authentication. Access is least-privilege and time-bound, normally no longer than 90 days before sponsor recertification. Production administrator access follows the Privileged Infrastructure Access Policy and requires a separate ticket for each work window. Customer or employee data access also requires the applicable data-owner approval.

External connections must use approved gateways. Vendors may not create unmanaged accounts, route data to their own cloud storage, disable logging, or subcontract access without written approval. Test environments must use synthetic or masked data unless a documented production-data exception is approved.

## Review and offboarding

System owners review vendor accounts monthly for inactivity and expiration. The sponsor must notify the service desk immediately when work ends, a person changes assignments, or the agreement is suspended. Identities, tokens, keys, remote sessions, and physical badges must be disabled by the effective end time.

Emergency troubleshooting does not waive these controls. An incident commander may authorize a shorter expedited review, but the vendor still requires an identified sponsor, multi-factor authentication, session logging, limited targets, and retrospective review. Procurement approval alone never grants system access.
