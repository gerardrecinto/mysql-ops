// Jenkins pipeline: test, build the image, scan it, deploy to Kubernetes, undo the rollout on failure.
// The same steps run in GitHub Actions (.github/workflows/ci.yml) against a kind cluster.
// Not run on a Jenkins server.
pipeline {
  agent any
  options { timeout(time: 20, unit: 'MINUTES'); disableConcurrentBuilds() }
  environment {
    IMAGE = "registry.example.com/replication-watch:${env.GIT_COMMIT}"
  }
  stages {
    stage('Unit tests') {
      steps { sh 'python -m pip install -r requirements.txt && python -m pytest tests/test_watch.py' }
    }
    stage('Build image') {
      steps { sh 'docker build -t "$IMAGE" .' }
    }
    stage('Scan image') {
      steps { sh 'trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$IMAGE"' }
    }
    stage('Push') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'registry', usernameVariable: 'U', passwordVariable: 'P')]) {
          sh 'echo "$P" | docker login registry.example.com -u "$U" --password-stdin && docker push "$IMAGE"'
        }
      }
    }
    stage('Deploy') {
      steps {
        sh '''
          kubectl apply -f k8s/lab.yaml
          kubectl set image deployment/replication-watch watch="$IMAGE"
          kubectl rollout status deployment/replication-watch --timeout=90s
        '''
      }
    }
  }
  post {
    failure { sh 'kubectl rollout undo deployment/replication-watch || true' }
  }
}
