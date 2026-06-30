// Daily batch job for the X lane: cron_restart fires run_x.sh once a day, then it exits.
//   pm2 start ecosystem_x.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "x-question-generator",
      script: "./run_x.sh",
      interpreter: "bash",
      cwd: __dirname,
      autorestart: false,
      cron_restart: "30 6 * * *", // every day at 06:30 server time — adjust as needed
      time: true,
    },
  ],
};
