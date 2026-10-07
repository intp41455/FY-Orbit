package com.findyourself.sdk;

public class FindYourselfException extends RuntimeException {
    private int statusCode;

    public FindYourselfException(String message) {
        super(message);
    }

    public FindYourselfException(int statusCode, String message) {
        super("HTTP " + statusCode + ": " + message);
        this.statusCode = statusCode;
    }

    public int getStatusCode() {
        return statusCode;
    }
}
