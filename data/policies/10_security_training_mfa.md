---
policy_id: EPG-SECURITY-010
title: Security Training and Multi-Factor Authentication Policy
domain: security-awareness
---
# Security Training and Multi-Factor Authentication Policy

## Purpose and applicability

This fictional policy establishes minimum security-learning and authentication requirements for employees, contractors, vendors, and other users of Organization systems. Completion records and authentication status are access prerequisites, not optional administrative tasks.

## Training requirements

Users must complete foundational security and privacy training before receiving access and renew it annually. People in privileged, software-development, finance, HR, customer-support, procurement, or incident-response roles must complete role-specific modules assigned by Security or the relevant data owner. Additional training may be required after a material policy change or security event.

Managers receive reminders before a deadline, but remain responsible for ensuring completion. A user whose required training is more than 14 days overdue may have interactive access suspended until completion. An exception requires a documented business reason, compensating controls, an owner, and an expiration date. A manager's informal approval does not extend training status.

## Authentication requirements

Multi-factor authentication is required for remote access, cloud applications containing confidential data, administrative consoles, source repositories, finance and payroll systems, and any privileged action. Phishing-resistant authenticators are required for administrators and incident responders when supported. SMS may be used only as a documented temporary fallback when stronger methods are unavailable.

Users must not approve an unexpected prompt, share one-time codes, enroll another person's authenticator, or bypass device checks. Lost authenticators and suspicious prompts must be reported immediately. Help-desk recovery requires identity verification and is logged; urgent access does not permit an agent to disclose or reset a credential without verification.

## Service accounts and review

Non-human identities must use managed keys or workload identity rather than interactive MFA. They require an owner, least privilege, rotation or federation controls, and periodic review. Security monitors failed authentication, unusual enrollment, recovery events, and MFA bypasses. Temporary bypasses must be narrowly scoped, time-bound, and reviewed after use.
