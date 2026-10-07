package com.findyourself.sdk;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * FindYourself 标准 Java 客户端。
 * 纯 Java 17+ 标准库实现，轻量、零第三方强依赖。
 */
public class FindYourselfClient {

    private final String baseUrl;
    private final String token;
    private final String csrfToken;
    private final String tenantId;
    private final HttpClient httpClient;

    public FindYourselfClient(String baseUrl) {
        this(baseUrl, null, null, null);
    }

    public FindYourselfClient(String baseUrl, String token, String csrfToken, String tenantId) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.token = token;
        this.csrfToken = csrfToken;
        this.tenantId = tenantId;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    public String sendRequest(String method, String path, String jsonBody, boolean csrf) {
        try {
            HttpRequest.Builder builder = HttpRequest.newBuilder()
                    .uri(URI.create(baseUrl + path))
                    .timeout(Duration.ofSeconds(15))
                    .header("Accept", "application/json")
                    .header("Content-Type", "application/json");

            if (token != null && !token.isBlank()) {
                builder.header("Authorization", "Bearer " + token);
            }
            if (csrf && csrfToken != null && !csrfToken.isBlank()) {
                builder.header("X-CSRF-Token", csrfToken);
            }
            if (tenantId != null && !tenantId.isBlank()) {
                builder.header("X-Tenant-Id", tenantId);
            }

            if ("POST".equalsIgnoreCase(method)) {
                builder.POST(HttpRequest.BodyPublishers.ofString(jsonBody != null ? jsonBody : "{}"));
            } else if ("GET".equalsIgnoreCase(method)) {
                builder.GET();
            }

            HttpResponse<String> response = httpClient.send(builder.build(), HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 400) {
                throw new FindYourselfException(response.statusCode(), response.body());
            }
            return response.body();
        } catch (IOException | InterruptedException e) {
            throw new FindYourselfException("Request failed: " + e.getMessage());
        }
    }

    // --- 快捷业务方法 ---

    public String listPlugins(String query, String sortBy) {
        String q = query != null ? query : "";
        String s = sortBy != null ? sortBy : "score";
        return sendRequest("GET", "/api/plugins/marketplace?query=" + q + "&sort_by=" + s, null, false);
    }

    public String getTemplateHierarchy() {
        return sendRequest("GET", "/api/plugins/templates/hierarchy", null, false);
    }

    public String listTemplates(String sortBy) {
        String s = sortBy != null ? sortBy : "score";
        return sendRequest("GET", "/api/plugins/templates/market?sort_by=" + s, null, false);
    }

    public String generateEmbedUrl(String page, String userId, String theme) {
        return EmbedHelper.generateIframeEmbedUrl(baseUrl, page, tenantId, userId, token, theme);
    }
}
