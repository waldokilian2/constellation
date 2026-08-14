package com.example.legacy;

/**
 * Dead code fixture: referenced by nothing since the 2019 migration, but
 * kept around "just in case".  {@code find_dead_code} flags it.
 */
public class LegacyReport {

    public String format(String rows) {
        String out = "";
        for (String row : rows.split(";")) {
            out = out + row.toUpperCase() + "\n";
        }
        return out;
    }
}
