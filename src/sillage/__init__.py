"""Sillage - one shared memory for engineers, developers and AI agents.

Every finished job (a closed ticket, an agent investigation, a code session, a
merged fix) becomes an *episode*; close episodes are distilled into *lessons*;
a lesson reviewed by a human referent becomes a *fiche* that both engineers
read and agents call before acting.

The package is offline-first: ``sillage.kernel.protocols`` defines the ports,
each has a local implementation (tests, demo, replay bench) and an AWS one
(AgentCore Memory / Runtime / Gateway / Policy, Bedrock, DynamoDB, S3).
"""

from __future__ import annotations

__version__ = "0.1.0"
