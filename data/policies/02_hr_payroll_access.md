---
policy_id: EPG-HR-002
title: Human Resources and Payroll Access Policy
domain: hr-payroll
---
# Human Resources and Payroll Access Policy

## Purpose and scope

This fictional policy controls access to employee records, compensation data, benefits elections, payroll files, and workforce reports. It applies to Human Resources, Payroll, Finance reviewers, internal auditors, support engineers, and vendors that maintain the HR platform.

## Role-based access

Access must follow least privilege and be assigned through an approved HR system role. HR business partners may view records only for supported business units. Payroll specialists may update pay instructions during an active payroll cycle, while Finance reviewers receive read-only totals needed for reconciliation. Managers may view compensation information only for their direct reporting hierarchy. Support engineers must use masked test data unless a production defect cannot be diagnosed otherwise.

Every request must include a ticket, business purpose, requested role, affected population, manager approval, and HR data-owner approval. Access lasting longer than 30 days requires a stated end date and quarterly recertification. Contractors and payroll vendors receive time-bound access under the Vendor Access Policy and must use managed devices with multi-factor authentication.

## Restricted actions

Bulk download of employee or payroll data is disabled by default. An approved export must specify fields, recipients, transfer method, retention period, and deletion date. Payroll files must be transferred through the approved encrypted exchange; email attachments, consumer file-sharing services, and removable media are prohibited. Users must not search for coworkers out of curiosity or use workforce information for an unrelated decision.

## Monitoring and exceptions

The system records sign-ins, record views, changes to banking instructions, exports, and privilege assignments. Payroll banking changes require step-up authentication and separation of duties. Suspected misuse must be reported to Security and HR Privacy.

Emergency access may be approved by the HR data owner to correct a payroll-blocking incident. It must expire after the documented work window and receive retrospective review within one business day. Legal holds and investigations follow the Legal and Compliance Exception Policy rather than informal manager approval.
