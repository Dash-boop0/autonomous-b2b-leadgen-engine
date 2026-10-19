"""Agent registry (20 agents in four groups)."""

from .base import AgentContext, BaseAgent
from .campaign import (
    CampaignPlanningAgent,
    FollowUpAgent,
    OutreachOperationsAgent,
    PersonalisationAgent,
    ReplyClassificationAgent,
)
from .discovery import (
    AccountDiscoveryAgent,
    BuyingSignalAgent,
    ContactDiscoveryAgent,
    ContactVerificationAgent,
    DataEnrichmentAgent,
    LeadScoringAgent,
    PublicWebResearchAgent,
)
from .governance import (
    ComplianceGovernanceAgent,
    ICPFeedbackAgent,
    PerformanceAnalyticsAgent,
    WorkflowSupervisorAgent,
)
from .market import BuyerPersonaAgent, CompetitorGapAgent, ICPBuilderAgent, MarketResearchAgent

AGENTS: dict[str, type[BaseAgent]] = {
    a.name: a
    for a in [
        MarketResearchAgent,
        ICPBuilderAgent,
        BuyerPersonaAgent,
        CompetitorGapAgent,
        AccountDiscoveryAgent,
        PublicWebResearchAgent,
        ContactDiscoveryAgent,
        DataEnrichmentAgent,
        ContactVerificationAgent,
        LeadScoringAgent,
        BuyingSignalAgent,
        CampaignPlanningAgent,
        PersonalisationAgent,
        OutreachOperationsAgent,
        ReplyClassificationAgent,
        FollowUpAgent,
        PerformanceAnalyticsAgent,
        ICPFeedbackAgent,
        ComplianceGovernanceAgent,
        WorkflowSupervisorAgent,
    ]
}

__all__ = ["AGENTS", "AgentContext", "BaseAgent"]
