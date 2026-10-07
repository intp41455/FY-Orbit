package com.findyourself.sdk;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * 企业 WebApp iframe 嵌入 URL 生成器（A-生态兼容-01 · Java）。
 */
public class EmbedHelper {

    public static String generateIframeEmbedUrl(
            String baseUrl,
            String page,
            String tenantId,
            String userId,
            String token,
            String theme) {
        String cleanBase = baseUrl.replaceAll("/+$", "");
        String route = switch (page != null ? page.toLowerCase() : "marketplace") {
            case "marketplace", "plugins" -> "/plugins";
            case "templates" -> "/templates";
            case "workbench" -> "/workbench";
            case "observability" -> "/observability";
            default -> "/" + page;
        };

        Map<String, String> params = new HashMap<>();
        params.put("embed", "true");
        params.put("theme", theme != null ? theme : "light");
        if (tenantId != null && !tenantId.isBlank()) params.put("tenant_id", tenantId);
        if (userId != null && !userId.isBlank()) params.put("user_id", userId);
        if (token != null && !token.isBlank()) params.put("token", token);

        StringBuilder qs = new StringBuilder();
        for (Map.Entry<String, String> entry : params.entrySet()) {
            if (qs.length() > 0) qs.append("&");
            qs.append(URLEncoder.encode(entry.getKey(), StandardCharsets.UTF_8))
              .append("=")
              .append(URLEncoder.encode(entry.getValue(), StandardCharsets.UTF_8));
        }

        return cleanBase + route + "?" + qs.toString();
    }
}
