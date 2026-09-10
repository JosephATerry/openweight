---
policy_id: EPG-FINANCE-009
title: Finance and Payment Systems Access Policy
domain: finance-payments
---
# Finance and Payment Systems Access Policy

## Purpose and scope

This fictional policy controls access to general-ledger, treasury, billing, accounts-payable, payment, and banking-administration systems. It applies to Finance staff, business approvers, internal audit, support engineers, and approved payment vendors.

## Access and separation of duties

Access must be based on a documented finance role, approved by the worker's manager and the finance system owner, and reviewed quarterly. The request must state the legal entity, payment account, function, and duration. Multi-factor authentication and managed devices are required. Support engineers receive diagnostic access without payment authority whenever technically possible.

No person may both create and release the same payment, add a supplier and approve its first payment, or change bank instructions and approve the resulting transaction. High-risk changes require step-up authentication and independent callback verification using contact information already recorded in the supplier master. Approval through an email reply alone is insufficient.

## Data handling and monitoring

Payment files, bank details, tax records, invoices, and reconciliation reports are confidential. Exports must contain only necessary fields and follow the Data Export and Transfer Policy. Finance data must not be transferred to personal storage, consumer file sharing, or an unapproved analytics service. Production payment data must not be used in software testing.

Systems must log sign-ins, beneficiary changes, role changes, payment creation, approvals, releases, exports, and failed multi-factor authentication. Finance Operations reviews unusual payment timing, new recipients, threshold splitting, and changes followed by rapid release.

## Emergency access

Emergency access for a payment outage requires an incident record, finance system-owner approval, limited duration, and session logging. It does not remove separation of duties. If normal approval services are unavailable, two authorized Finance approvers must document the decision through the incident channel and complete retrospective review within one business day.
