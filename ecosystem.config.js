// pm2's declaration of spotify-to-led: the whole supervision policy for the Pi.
// Host-specific values (BOARD_TRANSPORT, IDOT_MAC) live in the env file named
// below, not here. Reload with `pm2 startOrReload ecosystem.config.js
// --update-env`, or pm2 keeps the environment from the app's first start.
module.exports = {
  apps: [
    {
      name: "spotify-to-led",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "-m spotify_to_led.main",
      interpreter: "none",
      env: {
        // Without it Python block-buffers stdout into pm2's pipe, and the last
        // lines before a crash or SIGKILL never reach the log.
        PYTHONUNBUFFERED: "1",
        SPOTIFY_TO_LED_ENV_FILE: "/etc/spotify-to-led/env",
      },
      // Room for uvicorn and the BLE disconnect to finish before SIGKILL.
      kill_timeout: 10000,
      // Startup crash loops slow down to 15 s retries instead of hammering.
      exp_backoff_restart_delay: 100,
      // One stable log file name across restarts, no pid suffix.
      merge_logs: true,
    },
  ],
};
