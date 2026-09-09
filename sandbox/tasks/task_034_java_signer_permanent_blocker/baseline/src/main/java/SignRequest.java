import java.net.HttpURLConnection;
import java.net.URI;

public final class SignRequest {
    public static void main(String[] args) throws Exception {
        String base = System.getenv("EVAL_FIXTURE_URL");
        if (base == null || base.isBlank()) throw new IllegalStateException("signer endpoint not configured");
        HttpURLConnection connection = (HttpURLConnection) URI.create(base + "/signer").toURL().openConnection();
        connection.setRequestProperty("X-Fixture-Token", System.getenv("EVAL_FIXTURE_TOKEN"));
        if (connection.getResponseCode() != 200) throw new IllegalStateException("controlled signer unavailable");
    }
}
