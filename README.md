# google-cloud-warranty-eval
Event-driven, multi-agent system built on Google Cloud for Agentic Security, Safety, and Trust Whitepaper. It automates the warranty claim lifecycle by transforming raw user submissions into verified entitlement actions. Using a Zero-Trust Case Manager orchestrator, the system securely coordinates between specialized agents (entitlement & logistics) to verify purchase history and generate resolution outcomes—all without exposing sensitive customer PII to the public-facing entry point.

## Overview

This project implements an agentic chain triggered by customer events. It is designed with a **zero-trust** security model in mind, ensuring that the public-facing orchestrator has minimal permissions.

1.  **Trigger**: A Cloud Pub/Sub message is published when a customer submits a claim form.
2.  **Orchestrator (Agent 1: Case Manager)**: 
    *   Hosted on **Vertex AI Agent Engine**.
    *   Categorizes issues and coordinates the "Entitlement" check.
    *   Operates in a Zero-Trust boundary with restricted IAM roles.
3.  **Specialized Agents**: 
    *   **Agent 2 (Entitlement Guardian)**: Verifies purchase history and warranty status.
    *   **Agent 3 (Logistics Liaison)**: Generates shipping labels or discount codes.
4.  **Storage**: Interaction summaries are persisted to **Cloud Firestore** for audit and human-in-the-loop review.

## Prerequisites
* Google Cloud Project with Billing enabled.
* gcloud CLI installed and authenticated.
* Vertex AI and Pub/Sub APIs enabled.
