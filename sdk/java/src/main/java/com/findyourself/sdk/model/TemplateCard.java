package com.findyourself.sdk.model;

public class TemplateCard {
    private String templateId;
    private String name;
    private String scenario;
    private String summary;
    private String qualityTier;
    private int memberCount;
    private RatingSummary rating;

    public TemplateCard() {}

    public String getTemplateId() { return templateId; }
    public void setTemplateId(String templateId) { this.templateId = templateId; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getScenario() { return scenario; }
    public void setScenario(String scenario) { this.scenario = scenario; }

    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }

    public String getQualityTier() { return qualityTier; }
    public void setQualityTier(String qualityTier) { this.qualityTier = qualityTier; }

    public int getMemberCount() { return memberCount; }
    public void setMemberCount(int memberCount) { this.memberCount = memberCount; }

    public RatingSummary getRating() { return rating; }
    public void setRating(RatingSummary rating) { this.rating = rating; }
}
