package com.findyourself.sdk;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class ClientTest {

    @Test
    public void testEmbedUrlGeneration() {
        String url = EmbedHelper.generateIframeEmbedUrl(
                "http://localhost:8000",
                "marketplace",
                "acme-tenant",
                "user-99",
                "jwt-token",
                "dark"
        );

        assertNotNull(url);
        assertTrue(url.contains("http://localhost:8000/plugins?"));
        assertTrue(url.contains("tenant_id=acme-tenant"));
        assertTrue(url.contains("user_id=user-99"));
        assertTrue(url.contains("theme=dark"));
        assertTrue(url.contains("embed=true"));
    }

    @Test
    public void testClientInstantiation() {
        FindYourselfClient client = new FindYourselfClient("http://localhost:8000", "my-tok", "csrf-123", "tenant-1");
        assertNotNull(client);
        String embedUrl = client.generateEmbedUrl("templates", "u1", "light");
        assertTrue(embedUrl.contains("/templates?"));
        assertTrue(embedUrl.contains("tenant_id=tenant-1"));
    }
}
