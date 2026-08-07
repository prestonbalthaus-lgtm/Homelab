package homelab;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small JSON reader, enough to parse the topology feed.
 *
 * Java 8 has no JSON parser in the standard library and the applet has no
 * third-party dependencies, so this is hand rolled. It produces the usual
 * mapping: object to Map, array to List, string to String, number to Double,
 * literals to Boolean and null.
 */
final class Json {

    private final String src;
    private int pos;

    private Json(String src) {
        this.src = src;
    }

    static Object parse(String text) {
        if (text == null) {
            throw new IllegalArgumentException("no input");
        }
        Json reader = new Json(text);
        reader.skipWhitespace();
        Object value = reader.readValue();
        reader.skipWhitespace();
        if (reader.pos < reader.src.length()) {
            throw new IllegalArgumentException("trailing content at offset " + reader.pos);
        }
        return value;
    }

    private void skipWhitespace() {
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                pos++;
            } else {
                return;
            }
        }
    }

    private char peek() {
        if (pos >= src.length()) {
            throw new IllegalArgumentException("unexpected end of input");
        }
        return src.charAt(pos);
    }

    private Object readValue() {
        char c = peek();
        switch (c) {
            case '{': return readObject();
            case '[': return readArray();
            case '"': return readString();
            case 't': expect("true");  return Boolean.TRUE;
            case 'f': expect("false"); return Boolean.FALSE;
            case 'n': expect("null");  return null;
            default:  return readNumber();
        }
    }

    private Map<String, Object> readObject() {
        Map<String, Object> map = new LinkedHashMap<String, Object>();
        pos++;
        skipWhitespace();
        if (peek() == '}') {
            pos++;
            return map;
        }
        while (true) {
            skipWhitespace();
            String key = readString();
            skipWhitespace();
            if (peek() != ':') {
                throw new IllegalArgumentException("expected ':' at offset " + pos);
            }
            pos++;
            skipWhitespace();
            map.put(key, readValue());
            skipWhitespace();
            char c = peek();
            if (c == ',') {
                pos++;
            } else if (c == '}') {
                pos++;
                return map;
            } else {
                throw new IllegalArgumentException("expected ',' or '}' at offset " + pos);
            }
        }
    }

    private List<Object> readArray() {
        List<Object> list = new ArrayList<Object>();
        pos++;
        skipWhitespace();
        if (peek() == ']') {
            pos++;
            return list;
        }
        while (true) {
            skipWhitespace();
            list.add(readValue());
            skipWhitespace();
            char c = peek();
            if (c == ',') {
                pos++;
            } else if (c == ']') {
                pos++;
                return list;
            } else {
                throw new IllegalArgumentException("expected ',' or ']' at offset " + pos);
            }
        }
    }

    private String readString() {
        if (peek() != '"') {
            throw new IllegalArgumentException("expected a string at offset " + pos);
        }
        pos++;
        StringBuilder out = new StringBuilder();
        while (true) {
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unterminated string");
            }
            char c = src.charAt(pos++);
            if (c == '"') {
                return out.toString();
            }
            if (c != '\\') {
                out.append(c);
                continue;
            }
            if (pos >= src.length()) {
                throw new IllegalArgumentException("unterminated escape");
            }
            char esc = src.charAt(pos++);
            switch (esc) {
                case '"':  out.append('"');  break;
                case '\\': out.append('\\'); break;
                case '/':  out.append('/');  break;
                case 'b':  out.append('\b'); break;
                case 'f':  out.append('\f'); break;
                case 'n':  out.append('\n'); break;
                case 'r':  out.append('\r'); break;
                case 't':  out.append('\t'); break;
                case 'u':
                    if (pos + 4 > src.length()) {
                        throw new IllegalArgumentException("truncated \\u escape");
                    }
                    out.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                    pos += 4;
                    break;
                default:
                    throw new IllegalArgumentException("unsupported escape \\" + esc);
            }
        }
    }

    private Double readNumber() {
        int start = pos;
        if (pos < src.length() && (src.charAt(pos) == '-' || src.charAt(pos) == '+')) {
            pos++;
        }
        while (pos < src.length()) {
            char c = src.charAt(pos);
            if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E'
                    || c == '+' || c == '-') {
                pos++;
            } else {
                break;
            }
        }
        if (start == pos) {
            throw new IllegalArgumentException("expected a value at offset " + start);
        }
        try {
            return Double.valueOf(src.substring(start, pos));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("bad number at offset " + start);
        }
    }

    private void expect(String literal) {
        if (!src.startsWith(literal, pos)) {
            throw new IllegalArgumentException("expected " + literal + " at offset " + pos);
        }
        pos += literal.length();
    }
}
