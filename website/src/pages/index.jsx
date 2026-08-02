import Heading from "@theme/Heading";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";

import styles from "./index.module.css";

const features = [
  {
    icon: "◉",
    tone: "cyan",
    title: "Watch locally",
    text: "View the live RTSP feed and save full-resolution snapshots without a vendor cloud relay.",
  },
  {
    icon: "↶",
    tone: "amber",
    title: "Rewind the day",
    text: "Scrub across continuous camera recordings and open short, browser-ready preview windows.",
  },
  {
    icon: "◫",
    tone: "violet",
    title: "Track time-lapses",
    text: "Review the native capture schedule and preview every frame collected so far.",
  },
  {
    icon: "⇩",
    tone: "green",
    title: "Browse camera files",
    text: "Search both storage partitions and download closed recordings with safe filenames.",
  },
];

function CameraMockup() {
  return (
    <div className={styles.cameraShell} aria-label="GrowCam PC dashboard preview">
      <div className={styles.cameraTopbar}>
        <span><i className={styles.liveDot} /> LIVE</span>
        <span className={styles.connected}>Connected</span>
      </div>
      <div className={styles.cameraView}>
        <div className={styles.plantStem} />
        <div className={`${styles.leaf} ${styles.leafOne}`} />
        <div className={`${styles.leaf} ${styles.leafTwo}`} />
        <div className={`${styles.leaf} ${styles.leafThree}`} />
        <div className={styles.scanline} />
      </div>
      <div className={styles.cameraStats}>
        <span><strong>24h</strong> rewind</span>
        <span><strong>60%</strong> free</span>
        <span><strong>5m</strong> preview</span>
      </div>
    </div>
  );
}

function Home() {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout title={siteConfig.title} description={siteConfig.tagline}>
      <main>
        <header className={styles.hero}>
          <div className={styles.heroGlow} />
          <div className={styles.heroInner}>
            <div className={styles.heroCopy}>
              <p className={styles.kicker}>LOCAL-FIRST CAMERA CONTROL</p>
              <Heading as="h1">Your GrowCam.<br /><span>Your network.</span></Heading>
              <p className={styles.lead}>
                Live video, daily rewind, time-lapse progress, and camera file downloads from one private dashboard.
              </p>
              <div className={styles.actions}>
                <Link className="button button--primary button--lg" to="/docs/getting-started">Install GrowCam PC</Link>
                <Link className="button button--secondary button--lg" to="/docs/dashboard">Explore the dashboard</Link>
              </div>
              <div className={styles.installLine}>
                <code>uv tool install growcam-pc</code>
                <span>Python 3.11+ · Windows · macOS · Linux</span>
              </div>
            </div>
            <CameraMockup />
          </div>
        </header>

        <section className={styles.featureSection}>
          <div className={styles.sectionIntro}>
            <p className={styles.kicker}>ONE LOCAL TOOL</p>
            <Heading as="h2">The useful parts of the camera app, on your PC.</Heading>
          </div>
          <div className={styles.featureGrid}>
            {features.map((feature) => (
              <article className={styles.featureCard} key={feature.title}>
                <span className={`${styles.featureIcon} ${styles[feature.tone]}`}>{feature.icon}</span>
                <Heading as="h3">{feature.title}</Heading>
                <p>{feature.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className={styles.localSection}>
          <div>
            <p className={styles.kicker}>PRIVATE BY DEFAULT</p>
            <Heading as="h2">The browser talks to your computer. Your computer talks to the camera.</Heading>
          </div>
          <div className={styles.flow} aria-label="Local connection flow">
            <span>Browser</span><b>→</b><span>GrowCam PC</span><b>→</b><span>Camera LAN</span>
          </div>
          <p>No account, hosted dashboard, or remote relay is required. The web server binds to loopback unless you explicitly allow a network bind.</p>
        </section>
      </main>
    </Layout>
  );
}

export default Home;
