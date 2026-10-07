package com.findyourself.sdk.model;

import java.util.List;

public class PluginCard {
    private String skillId;
    private String name;
    private String version;
    private String domain;
    private String riskLevel;
    private List<String> capabilities;
    private RatingSummary rating;

    public PluginCard() {}

    public String getSkillId() { return skillId; }
    public void setSkillId(String skillId) { this.skillId = skillId; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getVersion() { return version; }
    public void setVersion(String version) { this.version = version; }

    public String getDomain() { return domain; }
    public void setDomain(String domain) { this.domain = domain; }

    public String getRiskLevel() { return riskLevel; }
    public void setRiskLevel(String riskLevel) { this.riskLevel = riskLevel; }

    public List<String> getCapabilities() { return capabilities; }
    public void setCapabilities(List<String> capabilities) { this.capabilities = capabilities; }

    public RatingSummary getRating() { return rating; }
    public void setRating(RatingSummary rating) { this.rating = rating; }
}
