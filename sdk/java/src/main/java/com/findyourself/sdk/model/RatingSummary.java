package com.findyourself.sdk.model;

import java.util.Map;

public class RatingSummary {
    private double averageRating;
    private int ratingCount;
    private double score;
    private Map<String, Integer> distribution;

    public RatingSummary() {}

    public RatingSummary(double averageRating, int ratingCount, double score, Map<String, Integer> distribution) {
        this.averageRating = averageRating;
        this.ratingCount = ratingCount;
        this.score = score;
        this.distribution = distribution;
    }

    public double getAverageRating() { return averageRating; }
    public void setAverageRating(double averageRating) { this.averageRating = averageRating; }

    public int getRatingCount() { return ratingCount; }
    public void setRatingCount(int ratingCount) { this.ratingCount = ratingCount; }

    public double getScore() { return score; }
    public void setScore(double score) { this.score = score; }

    public Map<String, Integer> getDistribution() { return distribution; }
    public void setDistribution(Map<String, Integer> distribution) { this.distribution = distribution; }
}
