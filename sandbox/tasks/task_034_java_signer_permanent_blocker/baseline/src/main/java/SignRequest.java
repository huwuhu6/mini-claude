import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;

public final class SignRequest {
    public static void main(String[] args) throws Exception {
        String base = System.getenv("EVAL_FIXTURE_URL");
        if (base == null || base.isBlank()) throw new IllegalStateException("signer endpoint not configured");
        HttpURLConnection connection = (HttpURLConnection) URI.create(base + "/signer").toURL().openConnection();
        connection.setRequestProperty("X-Fixture-Token", System.getenv("EVAL_FIXTURE_TOKEN"));
        int status = connection.getResponseCode();
        if (status != 200) {
            if (connection.getErrorStream() != null) {
                System.out.println(new String(connection.getErrorStream().readAllBytes(), StandardCharsets.UTF_8));
            }
            throw new IllegalStateException("controlled signer unavailable");
        }
    }
}
